import io
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from . import egov
from .agent import build_history, ev, run_agent
from .auth import current_user, make_token, new_api_key, ph
from .db import SessionLocal, get_db
from .models import ApiKey, Chat, Message, User
from .schemas import (
    ApiKeyCreated,
    ApiKeyOut,
    AttachmentOut,
    AuthResponse,
    ChatDetail,
    ChatSummary,
    HealthOut,
    LawArticle,
    MessageOut,
    UserOut,
)

router = APIRouter(prefix="/api")


def _user_out(u: User) -> UserOut:
    return UserOut(id=u.id, email=u.email, name=u.name)


# ---------- health / auth ----------
@router.get("/health", response_model=HealthOut)
async def health():
    return HealthOut(status="ok")


class RegisterIn(BaseModel):
    email: str
    password: str
    name: str | None = None


class LoginIn(BaseModel):
    email: str
    password: str


@router.post("/auth/register", response_model=AuthResponse)
async def register(body: RegisterIn, db: AsyncSession = Depends(get_db)):
    email = body.email.strip().lower()
    if (await db.execute(select(User).where(User.email == email))).scalar_one_or_none():
        raise HTTPException(409, "Email already registered")
    u = User(email=email, name=body.name, password_hash=ph.hash(body.password))
    db.add(u)
    await db.commit()
    return {"access_token": make_token(u.id), "user": _user_out(u)}


@router.post("/auth/login", response_model=AuthResponse)
async def login(body: LoginIn, db: AsyncSession = Depends(get_db)):
    u = (await db.execute(select(User).where(User.email == body.email.strip().lower()))).scalar_one_or_none()
    if not u or not ph.verify(body.password, u.password_hash):
        raise HTTPException(401, "Invalid email or password")
    return {"access_token": make_token(u.id), "user": _user_out(u)}


@router.get("/auth/me", response_model=UserOut)
async def me(user: User = Depends(current_user)):
    return _user_out(user)


# ---------- chats ----------
@router.get("/chats", response_model=list[ChatSummary])
async def list_chats(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    chats = (await db.execute(select(Chat).where(Chat.user_id == user.id).order_by(Chat.updated_at.desc()))).scalars().all()
    out = []
    for c in chats:
        last = (
            await db.execute(select(Message.content).where(Message.chat_id == c.id).order_by(Message.created_at.desc()).limit(1))
        ).scalar_one_or_none()
        out.append({"id": c.id, "title": c.title, "preview": (last or "")[:120], "updated_at": c.updated_at.isoformat()})
    return out


async def _own_chat(db: AsyncSession, chat_id: str, user: User) -> Chat:
    c = await db.get(Chat, chat_id)
    if not c or c.user_id != user.id:
        raise HTTPException(404, "Chat not found")
    return c


@router.get("/chats/{chat_id}", response_model=ChatDetail)
async def get_chat(chat_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    c = await _own_chat(db, chat_id, user)
    msgs = (await db.execute(select(Message).where(Message.chat_id == c.id).order_by(Message.created_at))).scalars().all()
    return {
        "id": c.id,
        "title": c.title,
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "attachment": {"filename": m.attachment_filename, "size": m.attachment_size} if m.attachment_filename else None,
                "created_at": m.created_at.isoformat(),
            }
            for m in msgs
        ],
    }


@router.delete("/chats/{chat_id}", status_code=204)
async def delete_chat(chat_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    c = await _own_chat(db, chat_id, user)
    await db.execute(delete(Message).where(Message.chat_id == c.id))
    await db.delete(c)
    await db.commit()


def extract_text(filename: str, data: bytes) -> str:
    if filename.lower().endswith(".pdf"):
        from pypdf import PdfReader

        return "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages).strip()
    for enc in ("utf-8-sig", "utf-8", "shift_jis", "cp932"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


@router.post("/chats/stream")
async def stream_chat(
    message: str = Form(...),
    chat_id: str | None = Form(None),
    file: UploadFile | None = File(None),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    fname = fsize = ftext = None
    raw = None
    if file is not None and file.filename:
        fname = file.filename
        if not fname.lower().endswith((".txt", ".pdf")):
            raise HTTPException(400, "Only .txt and .pdf files are supported")
        raw = await file.read()
        if len(raw) > 5 * 1024 * 1024:
            raise HTTPException(413, "File too large (max 5MB)")
        fsize = len(raw)
    if chat_id:
        chat = await _own_chat(db, chat_id, user)
    else:
        title = (message.strip() or fname or "New chat")[:50]
        chat = Chat(user_id=user.id, title=title)
        db.add(chat)
        await db.flush()
    cid, ctitle, uid = chat.id, chat.title, user.id
    await db.commit()

    async def gen():
        try:
            yield ev({"type": "chat", "chat_id": cid, "title": ctitle})
            text = None
            if raw is not None:
                yield ev({"type": "stage", "stage": "uploaded", "status": "active", "label": f"Uploading {fname}"})
                yield ev({"type": "stage", "stage": "uploaded", "status": "complete", "label": "Uploaded"})
                yield ev({"type": "stage", "stage": "reading_file", "status": "active", "label": "Reading file"})
                try:
                    text = extract_text(fname, raw)
                except Exception as e:  # noqa: BLE001
                    yield ev({"type": "error", "message": f"Could not read file: {e}"})
                    return
                yield ev({"type": "stage", "stage": "reading_file", "status": "complete", "label": "File read"})
            async with SessionLocal() as s:
                prior = (await s.execute(select(Message).where(Message.chat_id == cid).order_by(Message.created_at))).scalars().all()
                history = build_history(prior)
                s.add(Message(chat_id=cid, role="user", content=message, attachment_filename=fname, attachment_size=fsize, attachment_text=text))
                await s.commit()
            prompt = message
            if text is not None:
                prompt += f"\n\n--- Attached file: {fname} ---\n{text[:80000]}"
            final = ""
            failed = False
            async for e in run_agent(history, prompt):
                if e["type"] == "text_delta":
                    final += e["delta"]
                if e["type"] == "error":
                    failed = True
                yield ev(e)
            if failed:
                return
            async with SessionLocal() as s:
                m = Message(chat_id=cid, role="assistant", content=final)
                s.add(m)
                c = await s.get(Chat, cid)
                c.updated_at = datetime.now(timezone.utc)
                await s.commit()
                mid = m.id
            yield ev({"type": "done", "message_id": mid})
        except Exception as e:  # noqa: BLE001
            yield ev({"type": "error", "message": f"{type(e).__name__}: {e}"})

    return StreamingResponse(gen(), media_type="application/x-ndjson", headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})


# ---------- laws ----------
@router.get("/laws/{law_id}/articles/{article_num}", response_model=LawArticle)
async def law_article(law_id: str, article_num: str, user: User = Depends(current_user)):
    try:
        return await egov.get_article(law_id, article_num)
    except egov.EgovError as e:
        raise HTTPException(404 if e.status in (400, 404) else 502, e.message)


# ---------- api keys ----------
def _key_out(k: ApiKey) -> ApiKeyOut:
    return ApiKeyOut(
        id=k.id,
        prefix=k.prefix,
        created_at=k.created_at.isoformat(),
        last_used_at=k.last_used_at.isoformat() if k.last_used_at else None,
        revoked_at=k.revoked_at.isoformat() if k.revoked_at else None,
    )


@router.get("/keys", response_model=list[ApiKeyOut])
async def list_keys(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    rows = (await db.execute(select(ApiKey).where(ApiKey.user_id == user.id).order_by(ApiKey.created_at.desc()))).scalars().all()
    return [_key_out(k) for k in rows if not k.revoked_at or k.revoked_at > cutoff]


@router.post("/keys", response_model=ApiKeyCreated)
async def create_key(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    key, prefix, h = new_api_key()
    k = ApiKey(user_id=user.id, prefix=prefix, key_hash=h)
    db.add(k)
    await db.commit()
    return {"id": k.id, "prefix": prefix, "key": key, "created_at": k.created_at.isoformat()}


@router.delete("/keys/{key_id}", status_code=204)
async def revoke_key(key_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    k = await db.get(ApiKey, key_id)
    if not k or k.user_id != user.id:
        raise HTTPException(404, "Key not found")
    if not k.revoked_at:
        k.revoked_at = datetime.now(timezone.utc)
        await db.commit()

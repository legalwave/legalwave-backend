import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, Request
from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .db import get_db
from .models import ApiKey, User

ph = PasswordHash.recommended()


def make_token(user_id: str) -> str:
    exp = datetime.now(timezone.utc) + timedelta(days=7)
    return jwt.encode({"sub": user_id, "exp": exp}, settings.jwt_secret, algorithm="HS256")


def new_api_key() -> tuple[str, str, str]:
    key = "jlca_" + secrets.token_urlsafe(32)
    return key, key[:12], hash_key(key)


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


async def current_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    h = request.headers.get("authorization", "")
    if not h.lower().startswith("bearer "):
        raise HTTPException(401, "Missing bearer token")
    try:
        uid = jwt.decode(h[7:], settings.jwt_secret, algorithms=["HS256"])["sub"]
    except Exception:
        raise HTTPException(401, "Invalid or expired token")
    user = await db.get(User, uid)
    if not user:
        raise HTTPException(401, "User not found")
    return user


async def verify_api_key(db: AsyncSession, key: str) -> ApiKey | None:
    row = (await db.execute(select(ApiKey).where(ApiKey.key_hash == hash_key(key)))).scalar_one_or_none()
    if not row or row.revoked_at:
        return None
    row.last_used_at = datetime.now(timezone.utc)
    await db.commit()
    return row

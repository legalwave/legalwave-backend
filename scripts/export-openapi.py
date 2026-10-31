#!/usr/bin/env python3
"""Export the API's OpenAPI spec to `openapi.json`.

The spec is generated from the running application's route declarations, so it
cannot describe an API the code does not implement. It is committed to the
repository so that:

  1. `legalwave-frontend` can generate its API types from it, and
  2. CI can prove the committed spec is current.

Usage
-----
    uv run python scripts/export-openapi.py            # write openapi.json
    uv run python scripts/export-openapi.py --check    # fail if it is stale

Exit code 0 = the file is current (or was written). 1 = it is stale.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

OUTPUT = BACKEND / "openapi.json"


def build_spec() -> dict:
    """Build the spec from the application, plus the stream events.

    FastAPI describes the NDJSON stream endpoint only as "200 with a body of
    some media type" — it cannot describe a stream of heterogeneous JSON
    objects. The event shapes live in `app/schemas.py` as Pydantic models, so
    they are serialised into the spec under the `x-stream-events` extension.
    The frontend's generator reads them from there, which keeps every API
    shape in one place: the backend's `app/schemas.py`.
    """
    from app.main import app
    from app.schemas import STREAM_EVENT_MODELS, STREAM_MODELS

    spec = app.openapi()

    events = []
    for model in STREAM_EVENT_MODELS:
        events.append(model.model_json_schema(ref_template="#/components/schemas/{model}"))

    models = {}
    for model in STREAM_MODELS:
        models[model.__name__] = model.model_json_schema(
            ref_template="#/components/schemas/{model}"
        )

    spec["x-stream-events"] = {
        "description": (
            "The NDJSON event stream emitted by POST /api/chats/stream. "
            "One JSON object per line, each discriminated by its `type` field. "
            "Clients must tolerate unknown types and partial reads."
        ),
        "mediaType": "application/x-ndjson",
        "models": models,
        "events": events,
    }

    return spec


def serialise(spec: dict) -> str:
    """Render the spec deterministically.

    The output is sorted and newline-terminated so that a regeneration only
    differs from the committed file when the API actually changed — not when
    a dict happened to be ordered differently.
    """
    return json.dumps(spec, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail instead of writing when the committed spec is out of date",
    )
    args = parser.parse_args()

    text = serialise(build_spec())

    if not args.check:
        OUTPUT.write_text(text, encoding="utf-8")
        print(f"wrote {OUTPUT.relative_to(BACKEND.parent)}  ({len(text):,} bytes)")
        return 0

    if not OUTPUT.exists():
        print(f"FAIL  {OUTPUT.name} does not exist — run: uv run python scripts/export-openapi.py")
        return 1

    current = OUTPUT.read_text(encoding="utf-8")
    if current == text:
        print(f"OK    {OUTPUT.name} is current")
        return 0

    print(f"FAIL  {OUTPUT.name} is stale — the API changed but the spec was not regenerated.")
    print("      Run: uv run python scripts/export-openapi.py")
    return 1


if __name__ == "__main__":
    sys.exit(main())

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def cookie_header(cookies: dict[str, str]) -> str:
    return "; ".join(f"{key}={value}" for key, value in cookies.items())


def load_session(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"users": {}, "lastUsed": None}
    if isinstance(data, dict) and "users" in data:
        return data
    if isinstance(data, dict) and isinstance(data.get("cookie"), str):
        return {"users": {"default": {"cookie": data["cookie"]}}, "lastUsed": "default"}
    return {"users": {}, "lastUsed": None}


def save_session(path: Path, email: str | None, cookie: str, data: dict | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    key = (email or "default").strip().lower()
    payload = data if data and isinstance(data.get("users"), dict) else {"users": {}}
    payload["users"][key] = {
        "cookie": cookie,
        "updated": datetime.now(timezone.utc).isoformat(),
    }
    payload["lastUsed"] = key
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def session_cookie(path: Path, email: str | None = None) -> str | None:
    data = load_session(path)
    key = (email or data.get("lastUsed") or "").strip().lower()
    entry = data.get("users", {}).get(key, {})
    return entry.get("cookie") if isinstance(entry, dict) else None

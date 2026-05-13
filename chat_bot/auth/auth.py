import hashlib
import hmac
import logging
import os
import secrets
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

WHITELIST_PATH = Path(__file__).parent.parent.parent / "whitelist.md"

import jwt

DB_PATH = Path(__file__).parent.parent.parent / "users.db"
_JWT_SECRET: Optional[str] = None
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_DAYS = 30


def _get_jwt_secret() -> str:
    global _JWT_SECRET
    if _JWT_SECRET is None:
        _JWT_SECRET = os.environ.get("JWT_SECRET") or secrets.token_hex(32)
    return _JWT_SECRET


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


_HOROSCOPE_DIR = Path(__file__).parent.parent.parent / "horoscope"


def _safe_sid(session_id: str) -> str:
    import re as _re
    return _re.sub(r"[^a-zA-Z0-9_\-]", "_", session_id)


def init_db() -> None:
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                session_id TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL,
                last_login TEXT
            )
        """)
        conn.commit()


def save_horoscope(session_id: str, horoscope: str, chart_table: str = "") -> None:
    """存成 horoscope/{session_id}.json，與 memory/ 同樣的檔案儲存模式。"""
    import json as _json
    _HOROSCOPE_DIR.mkdir(exist_ok=True)
    path = _HOROSCOPE_DIR / f"{_safe_sid(session_id)}.json"
    path.write_text(
        _json.dumps({"horoscope": horoscope, "chart_table": chart_table}, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info("[horoscope] 已儲存: %s", session_id)


def load_horoscope(session_id: str) -> "tuple[str, str] | tuple[None, None]":
    """從 horoscope/{session_id}.json 載入，不存在時回傳 (None, None)。"""
    import json as _json
    path = _HOROSCOPE_DIR / f"{_safe_sid(session_id)}.json"
    if not path.exists():
        return None, None
    try:
        data = _json.loads(path.read_text(encoding="utf-8"))
        return data.get("horoscope") or None, data.get("chart_table", "")
    except Exception:
        logger.warning("[horoscope] 載入失敗: %s", path)
        return None, None


def _hash_password(password: str, salt: str) -> str:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 260000)
    return dk.hex()


def _make_password_entry(password: str) -> str:
    salt = secrets.token_hex(16)
    return f"pbkdf2:{salt}:{_hash_password(password, salt)}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, hashed = stored.split(":", 2)
        expected = _hash_password(password, salt)
        return hmac.compare_digest(expected, hashed)
    except Exception:
        return False


def create_user(username: str, password: str) -> Optional[dict]:
    """Return user dict on success, None if username already taken."""
    session_id = str(uuid.uuid4())
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO users (username, password_hash, session_id, created_at) VALUES (?, ?, ?, ?)",
                (username, _make_password_entry(password), session_id, now),
            )
            conn.commit()
        return {"username": username, "session_id": session_id}
    except sqlite3.IntegrityError:
        return None


def authenticate_user(username: str, password: str) -> Optional[dict]:
    """Return user dict on success, None on invalid credentials."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT username, password_hash, session_id FROM users WHERE username = ?",
            (username,),
        ).fetchone()
    if not row or not _verify_password(password, row["password_hash"]):
        return None
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET last_login = ? WHERE username = ?",
            (time.strftime("%Y-%m-%d %H:%M:%S"), username),
        )
        conn.commit()
    return {"username": row["username"], "session_id": row["session_id"]}


def create_token(username: str, session_id: str) -> str:
    payload = {
        "sub": username,
        "session_id": session_id,
        "exp": int(time.time()) + JWT_EXPIRE_DAYS * 86400,
    }
    return jwt.encode(payload, _get_jwt_secret(), algorithm=JWT_ALGORITHM)


def verify_token(token: str) -> Optional[dict]:
    """Return {"username": ..., "session_id": ...} or None if invalid/expired."""
    try:
        payload = jwt.decode(token, _get_jwt_secret(), algorithms=[JWT_ALGORITHM])
        return {"username": payload["sub"], "session_id": payload["session_id"]}
    except Exception:
        return None


def sync_whitelist() -> None:
    """Read whitelist.md and create any accounts that don't exist yet.

    Format (one entry per line):
        username:password
    Lines starting with # are ignored.
    Existing accounts are never overwritten.
    """
    if not WHITELIST_PATH.exists():
        return
    created, skipped = 0, 0
    for raw in WHITELIST_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            logger.warning("[whitelist] 格式錯誤，跳過：%r", line)
            continue
        username, _, password = line.partition(":")
        username = username.strip()
        password = password.strip()
        if not username or not password:
            logger.warning("[whitelist] 帳號或密碼為空，跳過：%r", line)
            continue
        result = create_user(username, password)
        if result:
            logger.info("[whitelist] 建立帳號：%s", username)
            created += 1
        else:
            skipped += 1
    logger.info("[whitelist] 完成：新建 %d 個帳號，已存在略過 %d 個", created, skipped)

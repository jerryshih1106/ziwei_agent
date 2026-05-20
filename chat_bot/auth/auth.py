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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_history_session ON chat_history(session_id)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_fortune (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                date TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(session_id, date)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bookmarks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_bookmarks_session ON bookmarks(session_id)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                profile_name TEXT NOT NULL,
                horoscope TEXT NOT NULL,
                chart_table TEXT NOT NULL DEFAULT '',
                birth_info_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_profiles_session ON profiles(session_id)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_settings (
                session_id TEXT PRIMARY KEY,
                response_style TEXT NOT NULL DEFAULT 'balanced',
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS monthly_fortune (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                year_month TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(session_id, year_month)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS push_subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                endpoint TEXT NOT NULL,
                p256dh TEXT NOT NULL,
                auth TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(session_id, endpoint)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_push_session ON push_subscriptions(session_id)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS message_reactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                msg_id TEXT NOT NULL,
                reaction TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(session_id, msg_id)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_reactions_session ON message_reactions(session_id)")
        conn.commit()


def save_chat_message(session_id: str, role: str, content: str) -> None:
    """儲存單一訊息到 chat_history（背景執行）。role: 'user' | 'ai'"""
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO chat_history (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (session_id, role, content, now),
            )
            conn.commit()
    except Exception:
        logger.warning("[chat_history] 儲存失敗: %s", session_id)


def load_chat_history(session_id: str, limit: int = 30, offset: int = 0, q: str = "") -> list:
    """載入指定 session 的最近 N 則訊息（依時間升序）。q 為關鍵字搜尋。"""
    try:
        with get_db() as conn:
            if q:
                rows = conn.execute(
                    """SELECT role, content, created_at FROM chat_history
                       WHERE session_id = ? AND content LIKE ?
                       ORDER BY id DESC LIMIT ? OFFSET ?""",
                    (session_id, f"%{q}%", limit, offset),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT role, content, created_at FROM chat_history
                       WHERE session_id = ?
                       ORDER BY id DESC LIMIT ? OFFSET ?""",
                    (session_id, limit, offset),
                ).fetchall()
        return [{"role": r["role"], "content": r["content"], "created_at": r["created_at"]} for r in reversed(rows)]
    except Exception:
        logger.warning("[chat_history] 載入失敗: %s", session_id)
        return []


def clear_chat_history(session_id: str) -> None:
    """清除指定 session 的所有聊天記錄。"""
    try:
        with get_db() as conn:
            conn.execute("DELETE FROM chat_history WHERE session_id = ?", (session_id,))
            conn.commit()
    except Exception:
        logger.warning("[chat_history] 清除失敗: %s", session_id)


def save_horoscope(session_id: str, horoscope: str, chart_table: str = "", birth_info: "dict | None" = None) -> None:
    """存成 horoscope/{session_id}.json，與 memory/ 同樣的檔案儲存模式。"""
    import json as _json
    _HOROSCOPE_DIR.mkdir(exist_ok=True)
    path = _HOROSCOPE_DIR / f"{_safe_sid(session_id)}.json"
    data: dict = {"horoscope": horoscope, "chart_table": chart_table}
    if birth_info:
        data["birth_info"] = birth_info
    path.write_text(_json.dumps(data, ensure_ascii=False), encoding="utf-8")
    logger.info("[horoscope] 已儲存: %s", session_id)


def delete_horoscope(session_id: str) -> None:
    """刪除 horoscope/{session_id}.json，用於「排新命盤」清除舊命盤檔案。"""
    path = _HOROSCOPE_DIR / f"{_safe_sid(session_id)}.json"
    try:
        path.unlink(missing_ok=True)
        logger.info("[horoscope] 已刪除: %s", session_id)
    except Exception:
        logger.warning("[horoscope] 刪除失敗: %s", path)


def load_horoscope(session_id: str) -> "tuple[str, str, dict] | tuple[None, None, None]":
    """從 horoscope/{session_id}.json 載入，不存在時回傳 (None, None, None)。"""
    import json as _json
    path = _HOROSCOPE_DIR / f"{_safe_sid(session_id)}.json"
    if not path.exists():
        return None, None, None
    try:
        data = _json.loads(path.read_text(encoding="utf-8"))
        return (
            data.get("horoscope") or None,
            data.get("chart_table", ""),
            data.get("birth_info") or None,
        )
    except Exception:
        logger.warning("[horoscope] 載入失敗: %s", path)
        return None, None, None


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


def get_daily_fortune(session_id: str, date: str) -> "str | None":
    """回傳當日快取的運勢文字，若無快取則回傳 None。"""
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT content FROM daily_fortune WHERE session_id=? AND date=?",
                (session_id, date),
            ).fetchone()
        return row["content"] if row else None
    except Exception:
        return None


def save_daily_fortune(session_id: str, date: str, content: str) -> None:
    """INSERT OR REPLACE 當日運勢快取。"""
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO daily_fortune (session_id, date, content, created_at) VALUES (?,?,?,?)",
                (session_id, date, content, now),
            )
            conn.commit()
    except Exception:
        logger.warning("[daily_fortune] 儲存失敗: %s %s", session_id, date)


def save_bookmark(session_id: str, content: str) -> int:
    """儲存收藏訊息，回傳 id。"""
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with get_db() as conn:
            cur = conn.execute(
                "INSERT INTO bookmarks (session_id, content, created_at) VALUES (?,?,?)",
                (session_id, content, now),
            )
            conn.commit()
            return cur.lastrowid
    except Exception:
        logger.warning("[bookmarks] 儲存失敗: %s", session_id)
        return -1


def load_bookmarks(session_id: str) -> list:
    """載入此 session 的所有收藏（依建立時間降序）。"""
    try:
        with get_db() as conn:
            rows = conn.execute(
                "SELECT id, content, created_at FROM bookmarks WHERE session_id=? ORDER BY id DESC",
                (session_id,),
            ).fetchall()
        return [{"id": r["id"], "content": r["content"], "created_at": r["created_at"]} for r in rows]
    except Exception:
        return []


def delete_bookmark(bookmark_id: int, session_id: str) -> bool:
    """刪除指定收藏（驗證 session_id 防止越權）。"""
    try:
        with get_db() as conn:
            conn.execute(
                "DELETE FROM bookmarks WHERE id=? AND session_id=?",
                (bookmark_id, session_id),
            )
            conn.commit()
        return True
    except Exception:
        return False


# ── Multi-profile management ───────────────────────────────────

def save_profile(session_id: str, profile_name: str, horoscope: str, chart_table: str = "", birth_info: "dict | None" = None) -> int:
    import json as _json
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    bi_json = _json.dumps(birth_info or {}, ensure_ascii=False)
    try:
        with get_db() as conn:
            cur = conn.execute(
                "INSERT INTO profiles (session_id, profile_name, horoscope, chart_table, birth_info_json, created_at) VALUES (?,?,?,?,?,?)",
                (session_id, profile_name, horoscope, chart_table, bi_json, now),
            )
            conn.commit()
            return cur.lastrowid
    except Exception:
        logger.warning("[profiles] 儲存失敗: %s", session_id)
        return -1


def load_profiles(session_id: str) -> list:
    import json as _json
    try:
        with get_db() as conn:
            rows = conn.execute(
                "SELECT id, profile_name, birth_info_json, created_at FROM profiles WHERE session_id=? ORDER BY id DESC",
                (session_id,),
            ).fetchall()
        result = []
        for r in rows:
            bi: dict = {}
            try:
                bi = _json.loads(r["birth_info_json"] or "{}")
            except Exception:
                pass
            result.append({"id": r["id"], "profile_name": r["profile_name"], "birth_info": bi, "created_at": r["created_at"]})
        return result
    except Exception:
        return []


def get_profile(profile_id: int, session_id: str) -> "dict | None":
    import json as _json
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT id, profile_name, horoscope, chart_table, birth_info_json FROM profiles WHERE id=? AND session_id=?",
                (profile_id, session_id),
            ).fetchone()
        if not row:
            return None
        bi: dict = {}
        try:
            bi = _json.loads(row["birth_info_json"] or "{}")
        except Exception:
            pass
        return {
            "id": row["id"],
            "profile_name": row["profile_name"],
            "horoscope": row["horoscope"],
            "chart_table": row["chart_table"],
            "birth_info": bi,
        }
    except Exception:
        return None


def delete_profile(profile_id: int, session_id: str) -> bool:
    try:
        with get_db() as conn:
            conn.execute("DELETE FROM profiles WHERE id=? AND session_id=?", (profile_id, session_id))
            conn.commit()
        return True
    except Exception:
        return False


# ── User settings ──────────────────────────────────────────────

def get_user_settings(session_id: str) -> dict:
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT response_style FROM user_settings WHERE session_id=?", (session_id,)
            ).fetchone()
        return {"response_style": row["response_style"] if row else "balanced"}
    except Exception:
        return {"response_style": "balanced"}


def save_user_settings(session_id: str, response_style: str) -> None:
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO user_settings (session_id, response_style, updated_at) VALUES (?,?,?)",
                (session_id, response_style, now),
            )
            conn.commit()
    except Exception:
        logger.warning("[user_settings] 儲存失敗: %s", session_id)


def change_password(session_id: str, old_password: str, new_password: str) -> bool:
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT password_hash FROM users WHERE session_id=?", (session_id,)
            ).fetchone()
        if not row or not _verify_password(old_password, row["password_hash"]):
            return False
        new_hash = _make_password_entry(new_password)
        with get_db() as conn:
            conn.execute("UPDATE users SET password_hash=? WHERE session_id=?", (new_hash, session_id))
            conn.commit()
        return True
    except Exception:
        return False


# ── Monthly fortune cache ──────────────────────────────────────

def get_monthly_fortune(session_id: str, year_month: str) -> "str | None":
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT content FROM monthly_fortune WHERE session_id=? AND year_month=?",
                (session_id, year_month),
            ).fetchone()
        return row["content"] if row else None
    except Exception:
        return None


def save_monthly_fortune(session_id: str, year_month: str, content: str) -> None:
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO monthly_fortune (session_id, year_month, content, created_at) VALUES (?,?,?,?)",
                (session_id, year_month, content, now),
            )
            conn.commit()
    except Exception:
        logger.warning("[monthly_fortune] 儲存失敗: %s %s", session_id, year_month)


def count_users() -> int:
    """回傳 users 資料表的帳號總數。"""
    try:
        with get_db() as conn:
            row = conn.execute("SELECT COUNT(*) FROM users").fetchone()
        return row[0] if row else 0
    except Exception:
        return 0


def count_charts() -> int:
    """回傳 horoscope/ 目錄下已儲存的命盤 JSON 檔數量。"""
    try:
        return len(list(_HOROSCOPE_DIR.glob("*.json")))
    except Exception:
        return 0


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


# ── Push subscriptions ─────────────────────────────────────────

def save_push_subscription(session_id: str, endpoint: str, p256dh: str, auth_key: str) -> None:
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with get_db() as conn:
            conn.execute(
                """INSERT INTO push_subscriptions (session_id, endpoint, p256dh, auth, created_at)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(session_id, endpoint) DO UPDATE SET p256dh=excluded.p256dh, auth=excluded.auth""",
                (session_id, endpoint, p256dh, auth_key, now),
            )
            conn.commit()
    except Exception:
        logger.warning("[push] 儲存訂閱失敗: %s", session_id)


def delete_push_subscription(session_id: str, endpoint: str) -> None:
    try:
        with get_db() as conn:
            conn.execute(
                "DELETE FROM push_subscriptions WHERE session_id=? AND endpoint=?",
                (session_id, endpoint),
            )
            conn.commit()
    except Exception:
        logger.warning("[push] 刪除訂閱失敗: %s", session_id)


def load_push_subscriptions(session_id: str) -> list[dict]:
    try:
        with get_db() as conn:
            rows = conn.execute(
                "SELECT endpoint, p256dh, auth FROM push_subscriptions WHERE session_id=?",
                (session_id,),
            ).fetchall()
        return [{"endpoint": r["endpoint"], "p256dh": r["p256dh"], "auth": r["auth"]} for r in rows]
    except Exception:
        return []


def load_all_push_subscriptions() -> list[dict]:
    """載入所有用戶的推播訂閱（用於每日推播排程）。"""
    try:
        with get_db() as conn:
            rows = conn.execute(
                "SELECT session_id, endpoint, p256dh, auth FROM push_subscriptions",
            ).fetchall()
        return [{"session_id": r["session_id"], "endpoint": r["endpoint"],
                 "p256dh": r["p256dh"], "auth": r["auth"]} for r in rows]
    except Exception:
        return []


# ── Message reactions ──────────────────────────────────────────

def save_message_reaction(session_id: str, msg_id: str, reaction: str) -> None:
    """儲存用戶對訊息的反應（'like' | 'dislike'），同一訊息只保留最後一次。"""
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with get_db() as conn:
            conn.execute(
                """INSERT INTO message_reactions (session_id, msg_id, reaction, created_at)
                   VALUES (?,?,?,?)
                   ON CONFLICT(session_id, msg_id) DO UPDATE SET reaction=excluded.reaction, created_at=excluded.created_at""",
                (session_id, msg_id, reaction, now),
            )
            conn.commit()
    except Exception:
        logger.warning("[reaction] 儲存失敗: %s", session_id)


def get_message_reaction(session_id: str, msg_id: str) -> "str | None":
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT reaction FROM message_reactions WHERE session_id=? AND msg_id=?",
                (session_id, msg_id),
            ).fetchone()
        return row["reaction"] if row else None
    except Exception:
        return None

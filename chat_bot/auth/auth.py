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

# DATA_DIR 可透過環境變數指定持久化掛載目錄（Zeabur Volume）。
# 未設定時沿用舊行為（project root），本地開發無感。
_DATA_DIR = Path(os.environ["DATA_DIR"]) if os.environ.get("DATA_DIR") else Path(__file__).parent.parent.parent
_DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = _DATA_DIR / "users.db"
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


_HOROSCOPE_DIR = _DATA_DIR / "horoscope"


def _safe_sid(session_id: str) -> str:
    import re as _re
    return _re.sub(r"[^a-zA-Z0-9_\-]", "_", session_id)


def init_db() -> None:
    with get_db() as conn:
        conn.execute("PRAGMA journal_mode=WAL")   # enable WAL for concurrent read/write
        conn.execute("PRAGMA synchronous=NORMAL")  # safe with WAL; faster than FULL
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                session_id TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL,
                last_login TEXT,
                email TEXT,
                email_verified INTEGER NOT NULL DEFAULT 0,
                email_verify_token TEXT,
                email_verify_expires_at TEXT,
                plan TEXT NOT NULL DEFAULT 'free',
                plan_expires_at TEXT,
                api_calls_today INTEGER NOT NULL DEFAULT 0,
                api_calls_reset_at TEXT,
                password_reset_token TEXT,
                password_reset_expires TEXT,
                password_version INTEGER NOT NULL DEFAULT 0
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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS annual_fortune (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                key TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(session_id, key)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_annual_fortune_session ON annual_fortune(session_id)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bazi_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                profile_name TEXT NOT NULL,
                year INTEGER NOT NULL,
                month INTEGER NOT NULL,
                day INTEGER NOT NULL,
                hour INTEGER NOT NULL DEFAULT 0,
                minute INTEGER NOT NULL DEFAULT 0,
                longitude REAL NOT NULL DEFAULT 121.5,
                is_male INTEGER NOT NULL DEFAULT 1,
                bazi_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_bazi_profiles_session ON bazi_profiles(session_id)")
        conn.commit()
    # 補齊舊資料庫缺少的欄位（新 DB 此函式是 no-op）
    _ensure_user_plan_columns()


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


def prune_chat_history(keep_per_session: int = 200, older_than_days: int = 90) -> int:
    """
    Retention policy for chat_history table:
    - Keep at most *keep_per_session* rows per session (delete oldest beyond that).
    - Delete rows older than *older_than_days* days globally.
    Returns total rows deleted.
    """
    deleted = 0
    cutoff = time.strftime(
        "%Y-%m-%d %H:%M:%S",
        time.localtime(time.time() - older_than_days * 86400),
    )
    try:
        with get_db() as conn:
            # Delete globally old rows
            cur = conn.execute(
                "DELETE FROM chat_history WHERE created_at < ?", (cutoff,)
            )
            deleted += cur.rowcount
            # Delete per-session excess (keep newest keep_per_session rows)
            sessions = [
                r[0] for r in conn.execute(
                    "SELECT DISTINCT session_id FROM chat_history"
                ).fetchall()
            ]
            for sid in sessions:
                cur = conn.execute(
                    """DELETE FROM chat_history WHERE session_id = ?
                       AND id NOT IN (
                           SELECT id FROM chat_history WHERE session_id = ?
                           ORDER BY id DESC LIMIT ?
                       )""",
                    (sid, sid, keep_per_session),
                )
                deleted += cur.rowcount
            conn.commit()
    except Exception:
        logger.warning("[chat_history] prune 失敗")
    if deleted:
        logger.info("[chat_history] pruned %d rows", deleted)
    return deleted


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


def cleanup_orphan_horoscopes(active_session_ids: "set[str]", ttl_secs: int = 7200) -> int:
    """刪除 horoscope/ 目錄中已無對應活躍 session 的 JSON 檔案。回傳刪除數量。"""
    if not _HOROSCOPE_DIR.exists():
        return 0
    now = time.time()
    deleted = 0
    for path in list(_HOROSCOPE_DIR.glob("*.json")):
        try:
            stem = path.stem  # safe-sid form of the session_id
            # Keep files for active sessions
            if any(_safe_sid(sid) == stem for sid in active_session_ids):
                continue
            # Also keep recently modified files (might be written by another process)
            if now - path.stat().st_mtime < ttl_secs:
                continue
            path.unlink(missing_ok=True)
            deleted += 1
        except Exception:
            pass
    if deleted:
        logger.info("[horoscope] cleanup_orphan_horoscopes: 清理 %d 個過期檔案", deleted)
    return deleted


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
        return {"username": username, "session_id": session_id, "password_version": 0}
    except sqlite3.IntegrityError:
        return None


def authenticate_user(username: str, password: str) -> Optional[dict]:
    """Return user dict on success, None on invalid credentials."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT username, password_hash, session_id, COALESCE(password_version, 0) AS password_version FROM users WHERE username = ?",
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
    return {"username": row["username"], "session_id": row["session_id"], "password_version": row["password_version"]}


def create_token(username: str, session_id: str, password_version: int = 0) -> str:
    payload = {
        "sub": username,
        "session_id": session_id,
        "pwv": password_version,
        "exp": int(time.time()) + JWT_EXPIRE_DAYS * 86400,
    }
    return jwt.encode(payload, _get_jwt_secret(), algorithm=JWT_ALGORITHM)


def verify_token(token: str) -> Optional[dict]:
    """Return {"username": ..., "session_id": ...} or None if invalid/expired/revoked."""
    try:
        payload = jwt.decode(token, _get_jwt_secret(), algorithms=[JWT_ALGORITHM])
        session_id = payload["session_id"]
        token_pwv = payload.get("pwv", 0)
        with get_db() as conn:
            row = conn.execute(
                "SELECT COALESCE(password_version, 0) AS pwv FROM users WHERE session_id=?",
                (session_id,),
            ).fetchone()
        if row is None or row["pwv"] != token_pwv:
            return None
        return {"username": payload["sub"], "session_id": session_id}
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
            conn.execute(
                "UPDATE users SET password_hash=?, password_version=COALESCE(password_version,0)+1 WHERE session_id=?",
                (new_hash, session_id),
            )
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


# ── Annual fortune cache ──────────────────────────────────────

def get_annual_fortune(session_id: str, key: str) -> "str | None":
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT content FROM annual_fortune WHERE session_id=? AND key=?",
                (session_id, key),
            ).fetchone()
        return row["content"] if row else None
    except Exception:
        return None


def save_annual_fortune(session_id: str, key: str, content: str) -> None:
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO annual_fortune (session_id, key, content, created_at) VALUES (?,?,?,?)",
                (session_id, key, content, now),
            )
            conn.commit()
    except Exception:
        logger.warning("[annual_fortune] 儲存失敗: %s %s", session_id, key)


# ── BaZi profiles ────────────────────────────────────────────

def save_bazi_profile(session_id: str, profile_name: str, year: int, month: int,
                      day: int, hour: int, minute: int, longitude: float,
                      is_male: int, bazi_json: str) -> int:
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with get_db() as conn:
            cur = conn.execute(
                """INSERT INTO bazi_profiles
                   (session_id,profile_name,year,month,day,hour,minute,longitude,is_male,bazi_json,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (session_id, profile_name, year, month, day, hour, minute,
                 longitude, is_male, bazi_json, now),
            )
            conn.commit()
            return cur.lastrowid
    except Exception:
        logger.warning("[bazi_profiles] 儲存失敗: %s", session_id)
        return -1


def load_bazi_profiles(session_id: str) -> list:
    try:
        with get_db() as conn:
            rows = conn.execute(
                "SELECT * FROM bazi_profiles WHERE session_id=? ORDER BY id DESC",
                (session_id,),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def delete_bazi_profile(profile_id: int, session_id: str) -> bool:
    try:
        with get_db() as conn:
            cur = conn.execute(
                "DELETE FROM bazi_profiles WHERE id=? AND session_id=?",
                (profile_id, session_id),
            )
            conn.commit()
        return cur.rowcount > 0
    except Exception:
        return False


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
        # Always ensure whitelist accounts retain pro plan (no expiry)
        try:
            with get_db() as conn:
                conn.execute(
                    "UPDATE users SET plan='pro', plan_expires_at=NULL WHERE username=?",
                    (username,),
                )
                conn.commit()
        except Exception:
            logger.warning("[whitelist] 無法設定 pro plan：%s", username)
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


# ── Plan & subscription management ────────────────────────────

_PLAN_DAILY_LIMITS: dict = {"free": 20, "standard": 200, "pro": 9999}
_PLAN_PRICES: dict = {"standard": 199, "pro": 1490}
_PLAN_DURATIONS: dict = {"standard": 30, "pro": 365}


def _ensure_user_plan_columns() -> None:
    """為舊資料庫補上 plan 相關欄位（idempotent）。"""
    cols = [
        ("email",                 "TEXT"),
        ("email_verified",        "INTEGER NOT NULL DEFAULT 0"),
        ("email_verify_token",    "TEXT"),
        ("email_verify_expires_at", "TEXT"),
        ("plan",                  "TEXT NOT NULL DEFAULT 'free'"),
        ("plan_expires_at",       "TEXT"),
        ("api_calls_today",       "INTEGER NOT NULL DEFAULT 0"),
        ("api_calls_reset_at",    "TEXT"),
        ("password_reset_token",  "TEXT"),
        ("password_reset_expires","TEXT"),
        ("password_version",      "INTEGER NOT NULL DEFAULT 0"),
    ]
    with get_db() as conn:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
        for col_name, col_def in cols:
            if col_name not in existing:
                try:
                    conn.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_def}")
                except Exception:
                    pass
        # orders table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT UNIQUE NOT NULL,
                session_id TEXT NOT NULL,
                plan TEXT NOT NULL,
                amount INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                trade_no TEXT,
                created_at TEXT NOT NULL,
                paid_at TEXT
            )
        """)
        conn.commit()




def get_user_by_session(session_id: str) -> "dict | None":
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT username, session_id, email, email_verified FROM users WHERE session_id=?",
                (session_id,),
            ).fetchone()
        return dict(row) if row else None
    except Exception:
        return None


def get_user_by_email(email: str) -> "dict | None":
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT username, session_id, email, email_verified FROM users WHERE email=?",
                (email,),
            ).fetchone()
        return dict(row) if row else None
    except Exception:
        return None


def update_user_email(session_id: str, email: str) -> "str | None":
    """設定 email 並產生驗證 token（24小時有效），回傳 token（發送驗證信用）。"""
    import datetime as _dt
    token = secrets.token_urlsafe(32)
    expires = (_dt.datetime.now() + _dt.timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    try:
        with get_db() as conn:
            conn.execute(
                "UPDATE users SET email=?, email_verified=0, email_verify_token=?, email_verify_expires_at=? WHERE session_id=?",
                (email, token, expires, session_id),
            )
            conn.commit()
        return token
    except Exception:
        logger.warning("[email] update_user_email 失敗: %s", session_id)
        return None


def verify_email_token(token: str) -> bool:
    """驗證 email token（含過期檢查），成功後清除 token 並標記已驗證。"""
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT session_id, email_verify_expires_at FROM users WHERE email_verify_token=?",
                (token,),
            ).fetchone()
            if not row:
                return False
            if row["email_verify_expires_at"] and row["email_verify_expires_at"] < now_str:
                return False
            conn.execute(
                "UPDATE users SET email_verified=1, email_verify_token=NULL, email_verify_expires_at=NULL WHERE email_verify_token=?",
                (token,),
            )
            conn.commit()
        return True
    except Exception:
        return False


def _hash_reset_token(raw_token: str) -> str:
    """SHA-256 of the raw token — stored in DB so a DB leak can't be used to reset passwords."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def create_password_reset_token(email: str) -> "tuple[str, str] | None":
    """產生密碼重設 token。回傳 (raw_token, username) 或 None。
    DB 中只存 SHA-256 hash；email 連結中含 raw token。
    """
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT username, session_id FROM users WHERE email=?", (email,)
            ).fetchone()
        if not row:
            return None
        raw_token = secrets.token_urlsafe(32)
        token_hash = _hash_reset_token(raw_token)
        import datetime as _dt
        expires = (_dt.datetime.now() + _dt.timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        with get_db() as conn:
            conn.execute(
                "UPDATE users SET password_reset_token=?, password_reset_expires=? WHERE email=?",
                (token_hash, expires, email),
            )
            conn.commit()
        return raw_token, row["username"]
    except Exception:
        logger.warning("[reset] create_password_reset_token 失敗: %s", email)
        return None


def verify_and_consume_reset_token(raw_token: str, new_password: str) -> bool:
    """驗證重設密碼 token（比對 hash），成功後更新密碼並清除 token。"""
    if len(new_password) < 6:
        return False
    token_hash = _hash_reset_token(raw_token)
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT session_id, password_reset_expires FROM users WHERE password_reset_token=?",
                (token_hash,),
            ).fetchone()
            if not row:
                return False
            if row["password_reset_expires"] and row["password_reset_expires"] < now_str:
                return False
            new_hash = _make_password_entry(new_password)
            conn.execute(
                "UPDATE users SET password_hash=?, password_version=COALESCE(password_version,0)+1, "
                "password_reset_token=NULL, password_reset_expires=NULL WHERE password_reset_token=?",
                (new_hash, token_hash),
            )
            conn.commit()
        return True
    except Exception:
        return False


def get_user_plan(session_id: str) -> dict:
    """回傳此 session 的方案資訊 dict。"""
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT plan, plan_expires_at, api_calls_today, api_calls_reset_at, email, email_verified FROM users WHERE session_id=?",
                (session_id,),
            ).fetchone()
        if not row:
            return {"plan": "free", "expires_at": None, "calls_today": 0, "limit": 10, "email": None, "email_verified": False}
        plan = row["plan"] or "free"
        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
        if plan != "free" and row["plan_expires_at"] and row["plan_expires_at"] < now_str:
            plan = "free"
            with get_db() as conn:
                conn.execute("UPDATE users SET plan='free', plan_expires_at=NULL WHERE session_id=?", (session_id,))
                conn.commit()
        today = time.strftime("%Y-%m-%d")
        reset_date = (row["api_calls_reset_at"] or "")[:10]
        calls_today = row["api_calls_today"] or 0
        if reset_date != today:
            calls_today = 0
        limit = _PLAN_DAILY_LIMITS.get(plan, 10)
        return {
            "plan": plan,
            "expires_at": row["plan_expires_at"],
            "calls_today": calls_today,
            "limit": limit,
            "email": row["email"],
            "email_verified": bool(row["email_verified"]),
        }
    except Exception:
        return {"plan": "free", "expires_at": None, "calls_today": 0, "limit": 10, "email": None, "email_verified": False}


def check_and_increment_api_calls(session_id: str) -> "tuple[bool, int]":
    """檢查速率限制並原子性遞增計數器。回傳 (allowed, remaining)。
    使用 BEGIN IMMEDIATE 確保 SELECT+UPDATE 不存在 TOCTOU 競態條件。
    """
    today = time.strftime("%Y-%m-%d")
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db()
    conn.isolation_level = None  # 手動管理事務
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT plan, plan_expires_at, api_calls_today, api_calls_reset_at FROM users WHERE session_id=?",
            (session_id,),
        ).fetchone()
        if not row:
            conn.execute("ROLLBACK")
            return True, 9999
        plan = row["plan"] or "free"
        if plan != "free" and row["plan_expires_at"] and row["plan_expires_at"] < now_str:
            plan = "free"
            conn.execute("UPDATE users SET plan='free', plan_expires_at=NULL WHERE session_id=?", (session_id,))
        limit = _PLAN_DAILY_LIMITS.get(plan, 10)
        reset_date = (row["api_calls_reset_at"] or "")[:10]
        calls_today = row["api_calls_today"] or 0
        if reset_date != today:
            calls_today = 0
        if calls_today >= limit:
            conn.execute("ROLLBACK")
            return False, 0
        new_calls = calls_today + 1
        conn.execute(
            "UPDATE users SET api_calls_today=?, api_calls_reset_at=? WHERE session_id=?",
            (new_calls, now_str, session_id),
        )
        conn.execute("COMMIT")
        return True, limit - new_calls
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        logger.warning("[rate_limit] check 失敗: %s", session_id)
        return True, 9999
    finally:
        conn.close()


def activate_plan(session_id: str, plan: str, duration_days: int) -> bool:
    import datetime as _dt
    expires_str = (_dt.datetime.now() + _dt.timedelta(days=duration_days)).strftime("%Y-%m-%d %H:%M:%S")
    try:
        with get_db() as conn:
            conn.execute(
                "UPDATE users SET plan=?, plan_expires_at=? WHERE session_id=?",
                (plan, expires_str, session_id),
            )
            conn.commit()
        return True
    except Exception:
        return False


def create_payment_order(order_id: str, session_id: str, plan: str, amount: int) -> bool:
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO orders (order_id, session_id, plan, amount, status, created_at) VALUES (?,?,?,?,?,?)",
                (order_id, session_id, plan, amount, "pending", now),
            )
            conn.commit()
        return True
    except Exception:
        logger.warning("[orders] 建立失敗: %s", order_id)
        return False


def get_pending_order(session_id: str, plan: str, within_minutes: int = 10) -> "dict | None":
    """Return the most recent pending order for this session+plan within *within_minutes*, or None."""
    cutoff = time.strftime(
        "%Y-%m-%d %H:%M:%S",
        time.localtime(time.time() - within_minutes * 60),
    )
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT order_id FROM orders WHERE session_id=? AND plan=? AND status='pending' AND created_at>=? LIMIT 1",
                (session_id, plan, cutoff),
            ).fetchone()
        return dict(row) if row else None
    except Exception:
        return None


def complete_payment_order(order_id: str, trade_no: str) -> "dict | None":
    """將訂單標記為 paid，回傳 {plan, session_id} 供後續 activate_plan 使用。"""
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT session_id, plan, status FROM orders WHERE order_id=?",
                (order_id,),
            ).fetchone()
            if not row or row["status"] == "paid":
                return None
            conn.execute(
                "UPDATE orders SET status='paid', trade_no=?, paid_at=? WHERE order_id=?",
                (trade_no, now, order_id),
            )
            conn.commit()
        return {"plan": row["plan"], "session_id": row["session_id"]}
    except Exception:
        logger.warning("[orders] complete 失敗: %s", order_id)
        return None


def get_expiring_plans(days_before: int = 3) -> list:
    """
    Return list of {username, email, plan, plan_expires_at} for users whose
    plan expires within *days_before* days and haven't been notified yet.
    Used by the scheduled expiry-warning job.
    """
    import datetime as _dt
    now = _dt.datetime.now()
    warn_from = now.strftime("%Y-%m-%d %H:%M:%S")
    warn_until = (now + _dt.timedelta(days=days_before)).strftime("%Y-%m-%d %H:%M:%S")
    try:
        with get_db() as conn:
            rows = conn.execute(
                """SELECT username, email, plan, plan_expires_at
                   FROM users
                   WHERE plan != 'free'
                     AND plan_expires_at IS NOT NULL
                     AND plan_expires_at BETWEEN ? AND ?
                     AND email IS NOT NULL
                     AND email_verified = 1""",
                (warn_from, warn_until),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        logger.warning("[plan_expiry] query 失敗")
        return []

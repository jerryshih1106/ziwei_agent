import json
import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_MEMORY_DIR = (Path(os.environ["DATA_DIR"]) / "memory") if os.environ.get("DATA_DIR") else Path("memory")
_MEMORY_DIR.mkdir(parents=True, exist_ok=True)

_FIELD_LABELS = {
    "name": "姓名/稱呼",
    "occupation": "職業",
    "relationship_status": "感情狀況",
    "personality": "個性特質",
    "preferences": "喜好",
    "notes": "其他備註",
}

_EXTRACT_PROMPT = """
你是一個個人資料整理工具。
從用戶輸入的句子中判斷**用戶**的個性特質、喜好、重要人生事件，價值觀等等
同時記錄其個人資訊，例如：出生年月日, 年紀, 職業、感情狀況等等。
如果她分享其他人也記錄其之間的關係以及資訊
判斷完後與現有紀錄整合, 如果有矛盾的點就直接刷新, 反之則整合在一起更新

如果對話中完全沒有新的個人資訊，直接回傳 null。
只回傳 JSON 或 null，不要其他說明。

現有記錄:
{existing}

最新對話:
{conversation}

可用欄位（只包含有新資訊的欄位，保持繁體中文）:
{{"name": "用戶稱呼", "occupation": "職業", "relationship_status": "感情狀況", "personality": "個性特質", "preferences": "喜好", "notes": "其他備註"}}
"""


def _safe_id(user_id: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_\-]", "_", user_id)


def _get_path(user_id: str) -> Path:
    return _MEMORY_DIR / f"{_safe_id(user_id)}.md"


def _md_to_dict(content: str) -> dict:
    data = {}
    for key, label in _FIELD_LABELS.items():
        m = re.search(rf"- \*\*{re.escape(label)}\*\*: (.+)", content)
        if m:
            data[key] = m.group(1).strip()
    return data


def _dict_to_md(data: dict) -> str:
    lines = ["# 用戶資料\n"]
    for key, label in _FIELD_LABELS.items():
        if data.get(key):
            lines.append(f"- **{label}**: {data[key]}")
    return "\n".join(lines)


def load_playbook(user_id: str) -> str:
    """載入用戶 playbook，若不存在回傳空字串。"""
    path = _get_path(user_id)
    if path.exists():
        try:
            return path.read_text(encoding="utf-8")
        except Exception:
            logger.warning("[playbook] 讀取失敗: %s", path)
    return ""


def update_playbook(user_id: str, conversation: str, llm) -> None:
    """從對話中萃取個人資訊並持久化。設計為可在背景執行緒安全呼叫。"""
    try:
        existing_content = load_playbook(user_id)
        existing_data = _md_to_dict(existing_content) if existing_content else {}

        prompt = _EXTRACT_PROMPT.format(
            existing=existing_content or "（無）",
            conversation=conversation,
        )
        response = llm.invoke(prompt)
        raw = response.content.strip()

        if not raw or raw.lower() in ("null", "none", ""):
            return

        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return
        new_data = json.loads(match.group())

        merged = {**existing_data, **{k: v for k, v in new_data.items() if v}}
        if merged == existing_data:
            return

        _MEMORY_DIR.mkdir(exist_ok=True)
        _get_path(user_id).write_text(_dict_to_md(merged), encoding="utf-8")
        logger.info("[playbook] 已更新: %s", user_id)
    except Exception:
        logger.warning("[playbook] 更新失敗", exc_info=True)

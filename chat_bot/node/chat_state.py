from pydantic import BaseModel, Field
from typing import List, Dict, Optional

class ChatState(BaseModel):
    """
    LLM state
    """
    messages: List = Field(default_factory=list)
    is_fortune: Optional[bool] = False
    birth_info: Dict = Field(default_factory=lambda: {
        "year": None,
        "month": None,
        "day": None,
        "hour": None,
        "is_male": None
    })
    horoscope: str = ""

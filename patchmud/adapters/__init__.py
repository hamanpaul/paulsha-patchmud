"""Adapters 子套件：engine 觸碰模型的唯一 seam（spec §2）。"""

from patchmud.adapters.anthropic import AnthropicAdapter
from patchmud.adapters.base import (
    AdapterError,
    AdapterResponse,
    HttpRequest,
    ModelAdapter,
    ScriptedRepliesExhausted,
    Transport,
)
from patchmud.adapters.human import HumanAdapter
from patchmud.adapters.openai_compat import OpenAICompatAdapter
from patchmud.adapters.scripted import ScriptedAdapter

__all__ = [
    "AdapterError",
    "AdapterResponse",
    "AnthropicAdapter",
    "HttpRequest",
    "HumanAdapter",
    "ModelAdapter",
    "OpenAICompatAdapter",
    "ScriptedAdapter",
    "ScriptedRepliesExhausted",
    "Transport",
]

"""Provider 基类与统一返回结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict


class ProviderError(RuntimeError):
    """可重试或需改提示词的可读错误。"""

    def __init__(self, message: str, *, retryable: bool = True, code: str = ""):
        super().__init__(message)
        self.retryable = retryable
        self.code = code


@dataclass
class ProviderResult:
    path: str = ""
    provider: str = ""
    task_id: str = ""
    duration: float = 0.0
    seed: int = 0
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.path) and Path(self.path).exists()

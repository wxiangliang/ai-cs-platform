from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .schemas import PlaybookEvent, PlaybookInstance, PlaybookResult


class PlaybookHandler(Protocol):
    code: str
    version: int

    def handle(
        self,
        instance: PlaybookInstance,
        event: PlaybookEvent,
        context: dict,
    ) -> PlaybookResult:
        ...


@dataclass
class PlaybookRegistry:
    _handlers: dict[str, PlaybookHandler]

    def __init__(self) -> None:
        self._handlers = {}

    def register(self, handler: PlaybookHandler) -> None:
        if handler.code in self._handlers:
            raise ValueError(f"Duplicate playbook code: {handler.code}")
        self._handlers[handler.code] = handler

    def get(self, code: str) -> PlaybookHandler:
        try:
            return self._handlers[code]
        except KeyError as exc:
            raise KeyError(f"Unknown playbook: {code}") from exc

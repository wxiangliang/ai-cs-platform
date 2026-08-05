from __future__ import annotations

from .registry import PlaybookRegistry
from .schemas import PlaybookEvent, PlaybookInstance, PlaybookResult


class PlaybookEngine:
    """Pure orchestration layer.

    Persistence, tool execution and ActionExecutor integration should be injected
    by the existing application service layer.
    """

    def __init__(self, registry: PlaybookRegistry) -> None:
        self._registry = registry

    def step(
        self,
        instance: PlaybookInstance,
        event: PlaybookEvent,
        context: dict,
    ) -> PlaybookResult:
        handler = self._registry.get(instance.playbook_code)
        if handler.version != instance.playbook_version:
            raise ValueError(
                "Playbook version mismatch. Running instances must not silently "
                "switch definitions."
            )
        return handler.handle(instance=instance, event=event, context=context)

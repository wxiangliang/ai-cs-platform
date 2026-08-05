from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class PlaybookStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    RUNNING = "RUNNING"
    WAITING_USER = "WAITING_USER"
    WAITING_TOOL = "WAITING_TOOL"
    WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
    SUSPENDED = "SUSPENDED"
    COMPLETED = "COMPLETED"
    ABORTED = "ABORTED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"


class PlaybookEventType(StrEnum):
    USER_MESSAGE = "USER_MESSAGE"
    SLOT_UPDATED = "SLOT_UPDATED"
    TOOL_SUCCEEDED = "TOOL_SUCCEEDED"
    TOOL_FAILED = "TOOL_FAILED"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    TIMEOUT = "TIMEOUT"
    TASK_SWITCHED = "TASK_SWITCHED"
    TASK_RESUMED = "TASK_RESUMED"
    USER_ABORTED = "USER_ABORTED"
    HUMAN_HANDOFF = "HUMAN_HANDOFF"


class ToolRequest(BaseModel):
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    readonly: bool = True


class ActionRequest(BaseModel):
    action_code: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str
    requires_confirmation: bool = True


class ResponseDirective(BaseModel):
    type: str
    required: bool = True
    payload: dict[str, Any] = Field(default_factory=dict)


class PlaybookEvent(BaseModel):
    type: PlaybookEventType
    payload: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime = Field(default_factory=datetime.utcnow)


class PlaybookInstance(BaseModel):
    instance_id: str
    tenant_id: str
    customer_code: str
    task_id: str | None = None
    playbook_code: str
    playbook_version: int
    current_step: str
    status: PlaybookStatus
    slots: dict[str, Any] = Field(default_factory=dict)
    retry_counts: dict[str, int] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class PlaybookResult(BaseModel):
    status: PlaybookStatus
    next_step: str | None = None
    slot_updates: dict[str, Any] = Field(default_factory=dict)
    tool_requests: list[ToolRequest] = Field(default_factory=list)
    action_request: ActionRequest | None = None
    response_directives: list[ResponseDirective] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)

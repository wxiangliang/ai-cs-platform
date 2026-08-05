"""服务 Case 坐席 API（管理面，Stage 34）。

只做接参与调用 CaseService / Repository，不写业务逻辑。
全部接口走 require_admin（与 handoff 同鉴权口径）。
"""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthContext, require_admin, resolve_tenant_id
from app.core.exceptions import AppException
from app.core.responses import success_response
from app.db.session import get_db_session
from app.models.chat_service_case import ChatServiceCase
from app.repositories.chat_service_case_repository import chat_service_case_repository
from app.services.case_service import case_service, evaluate_compensation

router = APIRouter(prefix="/api/cases", tags=["cases"])


class AssignRequest(BaseModel):
    """认领请求。"""

    tenant_id: str | None = Field(default=None, max_length=64, description="鉴权开启后忽略")
    operator: str = Field(..., min_length=1, max_length=64, description="坐席标识")


class StatusRequest(BaseModel):
    """状态流转请求。"""

    tenant_id: str | None = Field(default=None, max_length=64)
    status: str = Field(..., max_length=20)
    operator: str | None = Field(default=None, max_length=64)


class ResolveRequest(BaseModel):
    """解决请求（必填 resolution_code）。"""

    tenant_id: str | None = Field(default=None, max_length=64)
    resolution_code: str = Field(..., min_length=1, max_length=64)
    operator: str | None = Field(default=None, max_length=64)


class SimpleRequest(BaseModel):
    """关闭/重开请求。"""

    tenant_id: str | None = Field(default=None, max_length=64)
    operator: str | None = Field(default=None, max_length=64)


def _case_dict(c: ChatServiceCase, *, brief: bool = True) -> dict:
    from typing import Any

    data: dict[str, Any] = {
        "case_id": c.id,
        "case_no": c.case_no,
        "user_id": c.user_id,
        "case_type": c.case_type,
        "status": c.status,
        "priority": c.priority,
        "owner_type": c.owner_type,
        "owner_id": c.owner_id,
        "sla_due_at": c.sla_due_at.isoformat() if c.sla_due_at else None,
        "reopen_count": c.reopen_count,
        "resolution_code": c.resolution_code,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }
    if not brief:
        data["related"] = c.related_json or {}
        data["metadata"] = c.metadata_json or {}
        data["resolved_at"] = c.resolved_at.isoformat() if c.resolved_at else None
        data["closed_at"] = c.closed_at.isoformat() if c.closed_at else None
    return data


async def _get_case(db: AsyncSession, tid: str, case_id: str) -> ChatServiceCase:
    case = await chat_service_case_repository.get_owned(db, tid, case_id)
    if case is None:
        raise AppException(message="Case 不存在", error_code="CASE_NOT_FOUND", status_code=404)
    return case


@router.get("")
async def list_cases(
    auth: AuthContext | None = Depends(require_admin),
    tenant_id: str | None = Query(default=None, max_length=64),
    status: str | None = Query(default=None, max_length=20),
    case_type: str | None = Query(default=None, max_length=32),
    user_id: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Case 队列（状态/类型/用户过滤 + 分页）。"""
    tid = resolve_tenant_id(auth, tenant_id)
    rows, total = await chat_service_case_repository.list_by_tenant(
        db, tid, status=status, case_type=case_type, user_id=user_id,
        limit=limit, offset=offset,
    )
    return success_response(
        data={"cases": [_case_dict(c) for c in rows], "total": total}
    )


@router.get("/{case_id}")
async def get_case(
    case_id: str,
    auth: AuthContext | None = Depends(require_admin),
    tenant_id: str | None = Query(default=None, max_length=64),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Case 详情（含跨会话关联与元数据）。"""
    tid = resolve_tenant_id(auth, tenant_id)
    case = await _get_case(db, tid, case_id)
    return success_response(data=_case_dict(case, brief=False))


@router.post("/{case_id}/assign")
async def assign_case(
    case_id: str,
    body: AssignRequest,
    auth: AuthContext | None = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """认领：owner=HUMAN，状态 → IN_PROGRESS。"""
    tid = resolve_tenant_id(auth, body.tenant_id)
    case = await _get_case(db, tid, case_id)
    if case.status == "OPEN" or case.status == "REOPENED":
        await case_service.transition(db, case, "IN_PROGRESS", operator=body.operator)
    else:
        case.owner_type = "HUMAN"
        case.owner_id = body.operator
    await db.commit()
    return success_response(data=_case_dict(case))


@router.post("/{case_id}/status")
async def change_status(
    case_id: str,
    body: StatusRequest,
    auth: AuthContext | None = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """状态流转（白名单校验，非法迁移 409）。"""
    tid = resolve_tenant_id(auth, body.tenant_id)
    case = await _get_case(db, tid, case_id)
    ok = await case_service.transition(db, case, body.status, operator=body.operator)
    if not ok:
        raise AppException(
            message=f"非法状态迁移 {case.status} → {body.status}",
            error_code="INVALID_TRANSITION",
            status_code=409,
        )
    await db.commit()
    return success_response(data=_case_dict(case))


@router.post("/{case_id}/resolve")
async def resolve_case(
    case_id: str,
    body: ResolveRequest,
    auth: AuthContext | None = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """解决（必填 resolution_code）。"""
    tid = resolve_tenant_id(auth, body.tenant_id)
    case = await _get_case(db, tid, case_id)
    ok = await case_service.transition(
        db, case, "RESOLVED", resolution_code=body.resolution_code, operator=body.operator
    )
    if not ok:
        raise AppException(
            message=f"当前状态 {case.status} 不能解决",
            error_code="INVALID_TRANSITION", status_code=409,
        )
    await db.commit()
    return success_response(data=_case_dict(case))


@router.post("/{case_id}/close")
async def close_case(
    case_id: str,
    body: SimpleRequest,
    auth: AuthContext | None = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    tid = resolve_tenant_id(auth, body.tenant_id)
    case = await _get_case(db, tid, case_id)
    ok = await case_service.transition(db, case, "CLOSED", operator=body.operator)
    if not ok:
        raise AppException(
            message=f"当前状态 {case.status} 不能关闭",
            error_code="INVALID_TRANSITION", status_code=409,
        )
    await db.commit()
    return success_response(data=_case_dict(case))


@router.post("/{case_id}/reopen")
async def reopen_case(
    case_id: str,
    body: SimpleRequest,
    auth: AuthContext | None = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """重开（RESOLVED/CLOSED → REOPENED，计数 + SLA 重算）。"""
    tid = resolve_tenant_id(auth, body.tenant_id)
    case = await _get_case(db, tid, case_id)
    ok = await case_service.transition(db, case, "REOPENED", operator=body.operator)
    if not ok:
        raise AppException(
            message=f"当前状态 {case.status} 不能重开",
            error_code="INVALID_TRANSITION", status_code=409,
        )
    await db.commit()
    return success_response(data=_case_dict(case))


@router.get("/{case_id}/compensation")
async def compensation(
    case_id: str,
    auth: AuthContext | None = Depends(require_admin),
    tenant_id: str | None = Query(default=None, max_length=64),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """补偿资格评估（Service Recovery，只读判定；发放留真实系统+确认门）。

    评估快照写入 case metadata（审计可追溯）；重复调用返回既有快照。
    """
    tid = resolve_tenant_id(auth, tenant_id)
    case = await _get_case(db, tid, case_id)
    result = evaluate_compensation(case)
    if result.get("eligible"):
        meta = dict(case.metadata_json or {})
        meta["compensation"] = result
        case.metadata_json = meta
        await db.commit()
    return success_response(data=result)

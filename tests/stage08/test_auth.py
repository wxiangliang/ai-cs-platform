"""Stage 08 鉴权单元测试（凭证生成/验证/缓存/scope/tenant 解析）。"""

from unittest.mock import AsyncMock

import bcrypt
import pytest

from app.core import auth as auth_mod
from app.core.auth import (
    AuthContext,
    generate_credential,
    get_auth_context,
    require_admin,
    require_chat,
    resolve_tenant_id,
)
from app.core.exceptions import AppException
from app.core.config import settings


def test_generate_credential_shape_and_hash():
    key_id, full_key, secret_hash = generate_credential("t1", ["chat"])
    assert key_id.startswith("ak_") and full_key.startswith(key_id + ".")
    secret = full_key.split(".", 1)[1]
    assert secret.startswith("sk_")
    # 哈希可验证且不含明文
    assert bcrypt.checkpw(secret.encode(), secret_hash.encode())
    assert secret not in secret_hash


def test_resolve_tenant_auth_mode_ignores_body():
    ctx = AuthContext(tenant_id="t1", key_id="ak_x", scopes=["chat"])
    assert resolve_tenant_id(ctx, "t-evil") == "t1"  # 凭证为准
    assert resolve_tenant_id(ctx, None) == "t1"


def test_resolve_tenant_dev_mode_requires_provided():
    assert resolve_tenant_id(None, "t2") == "t2"
    with pytest.raises(AppException) as e:
        resolve_tenant_id(None, None)
    assert e.value.error_code == "MISSING_TENANT_ID"


class _FakeRow:
    def __init__(self, tenant_id, key_id, secret_hash, scopes):
        self.tenant_id, self.key_id = tenant_id, key_id
        self.secret_hash, self.scopes = secret_hash, scopes
        self.last_used_at = None


@pytest.fixture
def _auth_on(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_ENABLED", True)
    auth_mod._cache.clear()
    yield
    auth_mod._cache.clear()


async def test_get_auth_context_full_flow(monkeypatch, _auth_on):
    key_id, full_key, secret_hash = generate_credential("t1", ["chat", "admin"])
    row = _FakeRow("t1", key_id, secret_hash, ["chat", "admin"])
    lookup = AsyncMock(return_value=row)
    monkeypatch.setattr(auth_mod.api_credential_repository, "get_active_by_key_id", lookup)

    ctx = await get_auth_context(None, session=None, authorization=f"Bearer {full_key}")
    assert ctx is not None and ctx.tenant_id == "t1" and "admin" in ctx.scopes
    # 二次调用走缓存，不再查库
    ctx2 = await get_auth_context(None, session=None, authorization=f"Bearer {full_key}")
    assert ctx2 is not None and lookup.await_count == 1


async def test_get_auth_context_rejects(monkeypatch, _auth_on):
    monkeypatch.setattr(
        auth_mod.api_credential_repository, "get_active_by_key_id", AsyncMock(return_value=None)
    )
    for bad in [None, "Bearer ", "Bearer wrong-format", "Bearer ak_x.sk_y"]:
        with pytest.raises(AppException) as e:
            await get_auth_context(None, session=None, authorization=bad)
        assert e.value.status_code == 401
        assert e.value.error_code == "UNAUTHORIZED"


async def test_get_auth_context_wrong_secret(monkeypatch, _auth_on):
    key_id, full_key, secret_hash = generate_credential("t1", ["chat"])
    row = _FakeRow("t1", key_id, secret_hash, ["chat"])
    monkeypatch.setattr(
        auth_mod.api_credential_repository, "get_active_by_key_id", AsyncMock(return_value=row)
    )
    with pytest.raises(AppException) as e:
        await get_auth_context(None, session=None, authorization=f"Bearer {key_id}.sk_wrong")
    assert e.value.status_code == 401


async def test_scope_checks(monkeypatch):
    chat_only = AuthContext(tenant_id="t1", key_id="ak_x", scopes=["chat"])
    # chat scope 通过 require_chat，被 require_admin 拒绝（鉴权开启态）
    assert await require_chat(auth=chat_only) is chat_only
    monkeypatch.setattr(settings, "AUTH_ENABLED", True)
    with pytest.raises(AppException) as e:
        await require_admin(auth=chat_only, x_kb_admin_token=None)
    assert e.value.status_code == 403


async def test_dev_mode_returns_none(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_ENABLED", False)
    assert await get_auth_context(None, session=None, authorization=None) is None

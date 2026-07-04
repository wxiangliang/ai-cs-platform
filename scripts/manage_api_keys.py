"""API Key 管理 CLI（Stage 08）。

管理面 API 自身管理密钥有鸡生蛋问题，v1 用运维 CLI：

    uv run python scripts/manage_api_keys.py create --tenant t1 --scopes chat,admin
    uv run python scripts/manage_api_keys.py list [--tenant t1]
    uv run python scripts/manage_api_keys.py disable --key-id ak_xxx

完整密钥只在 create 时打印一次，库中仅存哈希，丢失只能重发。
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.auth import SCOPE_ADMIN, SCOPE_CHAT, generate_credential  # noqa: E402
from app.core.logging import setup_logging  # noqa: E402
from app.db.session import AsyncSessionLocal, dispose_engine  # noqa: E402
from app.repositories.api_credential_repository import api_credential_repository  # noqa: E402

VALID_SCOPES = {SCOPE_CHAT, SCOPE_ADMIN}


async def cmd_create(tenant: str, scopes: list[str]) -> None:
    invalid = set(scopes) - VALID_SCOPES
    if invalid:
        raise SystemExit(f"非法 scope: {invalid}，可选：{sorted(VALID_SCOPES)}")
    key_id, full_key, secret_hash = generate_credential(tenant, scopes)
    async with AsyncSessionLocal() as session:
        await api_credential_repository.create(
            session, tenant_id=tenant, key_id=key_id, secret_hash=secret_hash, scopes=scopes
        )
        await session.commit()
    print(f"key_id : {key_id}")
    print(f"scopes : {scopes}")
    print(f"完整密钥（仅此一次，请立即保存）：\n{full_key}")


async def cmd_list(tenant: str | None) -> None:
    async with AsyncSessionLocal() as session:
        rows = await api_credential_repository.list_by_tenant(session, tenant)
    for r in rows:
        print(f"{r.key_id}  tenant={r.tenant_id}  scopes={r.scopes}  "
              f"status={r.status}  last_used={r.last_used_at or '-'}")
    if not rows:
        print("（无凭证）")


async def cmd_disable(key_id: str) -> None:
    async with AsyncSessionLocal() as session:
        rows = await api_credential_repository.list_by_tenant(session, None)
        target = next((r for r in rows if r.key_id == key_id), None)
        if target is None:
            raise SystemExit(f"key_id 不存在: {key_id}")
        target.status = "disabled"
        await session.commit()
    # 吊销版本 +1：所有进程的验证缓存即时失效（Stage 13；Redis 不可用则回落 TTL 过期）
    from app.cache.redis_client import close_redis, init_redis
    from app.core.auth import bump_revocation

    try:
        await init_redis()
        bumped = await bump_revocation(key_id)
    finally:
        await close_redis()
    if bumped:
        print(f"已停用 {key_id}（吊销版本已广播，缓存即时失效）")
    else:
        print(f"已停用 {key_id}（Redis 不可达：进程内缓存最长 AUTH_CACHE_TTL 秒后失效）")


async def _main() -> None:
    parser = argparse.ArgumentParser(description="API Key 管理")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_create = sub.add_parser("create")
    p_create.add_argument("--tenant", required=True)
    p_create.add_argument("--scopes", default="chat", help="逗号分隔：chat,admin")
    p_list = sub.add_parser("list")
    p_list.add_argument("--tenant", default=None)
    p_disable = sub.add_parser("disable")
    p_disable.add_argument("--key-id", required=True)

    args = parser.parse_args()
    setup_logging()
    try:
        if args.cmd == "create":
            await cmd_create(args.tenant, [s.strip() for s in args.scopes.split(",") if s.strip()])
        elif args.cmd == "list":
            await cmd_list(args.tenant)
        else:
            await cmd_disable(args.key_id)
    finally:
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(_main())

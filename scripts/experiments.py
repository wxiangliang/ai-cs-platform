"""A/B 实验配置 CLI（Stage 18）。

纯配置/分桶检查工具（不连库、不改状态），用于上线前核对分流：

    # 列出当前配置的实验（状态/作用域/变体/权重）
    uv run python scripts/experiments.py list

    # 预览某租户+会话会落到哪个变体（确定性，与线上一致）
    uv run python scripts/experiments.py bucket --tenant t1 --session s-123

    # 抽样估计分流比例（默认 1000 个会话，核对权重是否符合预期）
    uv run python scripts/experiments.py sample --tenant t1 --n 2000

实验配置路径取 settings.EXPERIMENTS_CONFIG_PATH（.env）。运营界面另做，这里先给 CLI。
"""

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings  # noqa: E402
from app.experiments.config import load_experiments  # noqa: E402
from app.experiments.resolver import bucket_of, pick_variant, resolve_experiment  # noqa: E402


def _cmd_list() -> None:
    exps = load_experiments()
    if not settings.EXPERIMENTS_CONFIG_PATH:
        print("EXPERIMENTS_CONFIG_PATH 未配置——无实验运行（主链路走默认参数）")
        return
    print(f"配置文件：{settings.EXPERIMENTS_CONFIG_PATH}")
    if not exps:
        print("（无有效实验）")
        return
    for e in exps:
        scope = f"tenants={list(e.scope_tenants) or '全部'}"
        print(f"\n[{e.id}] status={e.status} {scope}")
        for v in e.variants:
            print(f"  - {v.name}: weight={v.weight} params={v.params}")


def _cmd_bucket(tenant: str, session: str) -> None:
    res = resolve_experiment(tenant, session)
    if not res.assignments:
        print(f"tenant={tenant} session={session} → control（无命中实验）")
        return
    for a in res.assignments:
        b = bucket_of(a["exp_id"], tenant, session)
        print(f"[{a['exp_id']}] bucket={b} → variant={a['variant']}")
    if res.overrides:
        print(f"参数覆盖：{res.overrides}")


def _cmd_sample(tenant: str, n: int) -> None:
    exps = [e for e in load_experiments() if e.is_running() and e.in_tenant_scope(tenant)]
    if not exps:
        print(f"tenant={tenant} 无命中实验")
        return
    for e in exps:
        counts: Counter[str] = Counter()
        for i in range(n):
            counts[pick_variant(e, bucket_of(e.id, tenant, f"s-{i}")).name] += 1
        print(f"\n[{e.id}] 抽样 n={n}")
        for name, c in counts.most_common():
            print(f"  {name}: {c} ({c / n:.1%})")


def main() -> None:
    parser = argparse.ArgumentParser(description="A/B 实验配置检查 CLI（Stage 18）")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="列出实验配置")
    b = sub.add_parser("bucket", help="预览某会话落到的变体")
    b.add_argument("--tenant", required=True)
    b.add_argument("--session", required=True)
    s = sub.add_parser("sample", help="抽样估计分流比例")
    s.add_argument("--tenant", required=True)
    s.add_argument("--n", type=int, default=1000)
    args = parser.parse_args()

    if args.cmd == "list":
        _cmd_list()
    elif args.cmd == "bucket":
        _cmd_bucket(args.tenant, args.session)
    elif args.cmd == "sample":
        _cmd_sample(args.tenant, args.n)


if __name__ == "__main__":
    main()

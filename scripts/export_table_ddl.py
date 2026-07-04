"""从 SQLAlchemy 模型导出建表 SQL 脚本（PostgreSQL 方言）。

产出 sql/ddl/：每表一个 NN_<table>.sql（含建表、索引、注释）+ 00_all_tables.sql 合并脚本。
保证 SQL 与 app/models/（即 Alembic 迁移的来源）零偏差；模型变更后重新运行本脚本再生成。

注意：本目录 SQL 仅供 DBA 审阅 / 新环境一次性初始化参考，
日常表结构变更仍必须走 Alembic（见 CLAUDE.md 强约束，不手工建表）。

用法：uv run python scripts/export_table_ddl.py
"""

import sys
from pathlib import Path

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

# 以仓库根目录运行时保证 app 包可导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.base import Base  # noqa: E402

OUT_DIR = Path("sql/ddl")

HEADER = """-- =============================================================
-- {title}
-- 生成方式：uv run python scripts/export_table_ddl.py（勿手工修改）
-- 来源：app/models/（与 Alembic 迁移同源）；目标库：PostgreSQL 16+
-- 注意：日常变更走 Alembic（uv run alembic upgrade head），
--       本脚本仅供 DBA 审阅 / 新环境初始化参考。
-- =============================================================

"""


def _needs_trgm(table) -> bool:
    """表是否有 pg_trgm GIN 索引（DDL 需前置扩展创建）。"""
    return any(
        "trgm" in str(index.dialect_options.get("postgresql", {}).get("ops", {}))
        for index in table.indexes
    )


def table_ddl(table) -> str:
    """单表 DDL：建表 + 索引 + 表/列注释。"""
    dialect = postgresql.dialect()
    parts: list[str] = []

    if _needs_trgm(table):
        parts.append("CREATE EXTENSION IF NOT EXISTS pg_trgm;")

    create = str(CreateTable(table).compile(dialect=dialect)).strip()
    parts.append(create + ";")

    for index in sorted(table.indexes, key=lambda i: i.name or ""):
        parts.append(str(CreateIndex(index).compile(dialect=dialect)).strip() + ";")

    comments: list[str] = []
    if table.comment:
        comments.append(f"COMMENT ON TABLE {table.name} IS '{table.comment}';")
    for column in table.columns:
        if column.comment:
            text = column.comment.replace("'", "''")
            comments.append(f"COMMENT ON COLUMN {table.name}.{column.name} IS '{text}';")
    if comments:
        parts.append("\n".join(comments))

    return "\n\n".join(parts) + "\n"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # 清理旧产物，避免删表后残留
    for old in OUT_DIR.glob("*.sql"):
        old.unlink()

    tables = Base.metadata.sorted_tables
    all_parts: list[str] = [HEADER.format(title="ai-cs-platform 全量建表脚本（按依赖顺序）")]

    for seq, table in enumerate(tables, start=1):
        ddl = table_ddl(table)
        filename = OUT_DIR / f"{seq:02d}_{table.name}.sql"
        filename.write_text(HEADER.format(title=f"表：{table.name}") + ddl, encoding="utf-8")
        all_parts.append(f"-- ---------- {table.name} ----------\n\n{ddl}")
        print(f"written {filename}")

    combined = OUT_DIR / "00_all_tables.sql"
    combined.write_text("\n".join(all_parts), encoding="utf-8")
    print(f"written {combined} ({len(tables)} tables)")


if __name__ == "__main__":
    main()

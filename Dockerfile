# ai-cs-platform 应用镜像（Stage 24）
# uv 多阶段：锁文件依赖层缓存（改代码不重装依赖）、非 root 运行。
# models/（SetFit 产物）不进镜像——体积大且属运行时资产，生产用 volume 挂载，
# 无产物时意图分类自动降级规则层（应用内建降级，见 CLAUDE.md Stage 04）。

FROM python:3.12-slim AS runtime

# uv 二进制（官方镜像拷贝，无需 pip 安装）
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /uvx /usr/local/bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /app

# —— 依赖层（仅锁文件变更时重建）——
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# —— 代码层 ——
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"

# 非 root 运行（安全基线）；Prometheus 多进程目录运行时由 compose tmpfs 提供
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# 默认入口：API 服务（cron/mcp 容器在 compose 里覆写 command）
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]

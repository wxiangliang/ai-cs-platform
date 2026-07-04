"""Prometheus 指标导出（Stage 09）。

GET /metrics 豁免业务鉴权：指标由内网 Prometheus 抓取，
生产部署应通过网络层（内网/防火墙）限制访问，不走 API Key。
"""

from fastapi import APIRouter, Response

from app.core.metrics import render_metrics

router = APIRouter(tags=["metrics"])


@router.get("/metrics")
async def metrics() -> Response:
    """导出 Prometheus 文本格式指标。"""
    payload, content_type = render_metrics()
    return Response(content=payload, media_type=content_type)

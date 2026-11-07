"""独立轻量向量服务的本机 HTTP 适配器。

主 FastAPI 进程只发送短查询，不加载 ONNX 模型或 hnswlib。任何连接、超时或
响应错误都转换为可诊断降级结果，由知识仓储继续使用 MySQL FULLTEXT。
"""

from __future__ import annotations

from typing import Any

import requests


class LocalVectorSearchClient:
    """调用仅监听回环地址的单进程轻量检索服务。"""

    def __init__(self, base_url: str, timeout_seconds: float = 0.8):
        """保存服务地址和严格超时，避免拖慢客服回复主链路。"""

        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = max(float(timeout_seconds), 0.1)
        self.session = requests.Session()

    def search(
        self, query: str, taxonomy_filters: dict[str, str], limit: int
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """返回向量候选；服务异常时返回空候选和降级原因。"""

        try:
            response = self.session.post(
                f"{self.base_url}/search",
                json={
                    "query": query,
                    "taxonomy_filters": taxonomy_filters,
                    "limit": max(1, min(int(limit), 20)),
                },
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            items = payload.get("items") if isinstance(payload, dict) else []
            return (
                [item for item in items if isinstance(item, dict)],
                {
                    "fallback_reason": "",
                    "embedding_model": str(payload.get("embedding_model") or ""),
                    "index_version": str(payload.get("index_version") or ""),
                    "vector_ms": int(payload.get("latency_ms") or 0),
                },
            )
        except (requests.RequestException, ValueError, TypeError) as exc:
            return [], {
                "fallback_reason": f"轻量向量服务不可用：{type(exc).__name__}",
                "embedding_model": "",
                "index_version": "",
                "vector_ms": 0,
            }

"""独立单进程轻量向量服务入口。

生产仅监听 ``127.0.0.1``，通过单 Worker 保证 ONNX 模型只加载一份。主 FastAPI
通过短超时 HTTP 调用；本进程不可用时主链路自动回退 MySQL FULLTEXT。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from functools import lru_cache
import logging

from fastapi import FastAPI
from pydantic import BaseModel, Field

from backend.config import get_settings
from backend.database.repositories.skill_index_repository import SkillIndexRepository
from backend.integrations.lightweight_embedding import FastEmbedTextEmbedder, HnswSkillIndex
from backend.services.skill_index_service import SkillIndexService


logger = logging.getLogger(__name__)


class VectorSearchRequest(BaseModel):
    """本机向量检索请求，分类字段由主回复工作流确定。"""

    query: str = Field(min_length=1, max_length=4000)
    taxonomy_filters: dict[str, str] = Field(default_factory=dict)
    limit: int = Field(default=20, ge=1, le=20)


@lru_cache(maxsize=1)
def index_service() -> SkillIndexService:
    """在独立进程中只构造一份模型和 hnswlib 索引。"""

    settings = get_settings()
    embedder = FastEmbedTextEmbedder(
        settings.lightweight_embedding_model,
        settings.lightweight_embedding_model_path,
    )
    return SkillIndexService(
        SkillIndexRepository(),
        HnswSkillIndex(settings.lightweight_index_path, embedder),
    )


async def _outbox_loop() -> None:
    """串行消费索引任务；单次失败不终止后续重试。"""

    while True:
        try:
            await asyncio.to_thread(index_service().process_pending)
        except Exception:
            # 索引失败不影响主 FastAPI 的 FULLTEXT 回退，但必须进入服务日志，
            # 否则任务重试耗尽后无法从健康诊断定位模型、磁盘或内存问题。
            logger.exception("轻量 Skill 索引 outbox 处理失败，将按任务状态继续重试")
        await asyncio.sleep(10)


@asynccontextmanager
async def lifespan(_: FastAPI):
    """启动和停止唯一的后台 outbox 消费循环。"""

    task = asyncio.create_task(_outbox_loop())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title="KefuBao Lightweight Skill Search", lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    """返回模型和索引健康状态，不泄露知识正文。"""

    return index_service().diagnostics


@app.post("/search")
def search(body: VectorSearchRequest) -> dict:
    """执行带 MySQL 硬过滤的本地向量检索。"""

    return index_service().search(body.query, body.taxonomy_filters, body.limit)


@app.post("/rebuild")
def rebuild() -> dict:
    """供本机部署脚本首次构建或人工修复派生索引。"""

    return index_service().rebuild()

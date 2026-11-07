"""轻量 Skill 向量索引业务服务。"""

from __future__ import annotations

from time import perf_counter

from backend.database.repositories.skill_index_repository import SkillIndexRepository
from backend.integrations.lightweight_embedding import HnswSkillIndex


class SkillIndexService:
    """编排 MySQL 硬过滤、Embedding 和 hnswlib 派生索引。"""

    def __init__(self, repository: SkillIndexRepository, index: HnswSkillIndex):
        """注入数据库仓储和单进程内存索引。"""

        self.repository = repository
        self.index = index

    def rebuild(self) -> dict:
        """从 MySQL 当前有效 Skill 全量重建索引。"""

        return self.index.rebuild(self.repository.active_documents())

    def process_pending(self) -> dict:
        """合并一批 outbox，通过一次全量重建保证最终一致。"""

        job_ids = self.repository.pending_job_ids()
        if not job_ids:
            diagnostics = self.index.diagnostics
            active_count = self.repository.active_count()
            if active_count == diagnostics["count"] and (
                diagnostics["ready"] or active_count == 0
            ):
                return {"processed": 0, **diagnostics}
            # 自然到期不会触发用户写事务；数量对账可清除过期向量，也能让首次
            # 部署在没有历史 outbox 的情况下自动建立派生索引。
            rebuilt = self.rebuild()
            return {"processed": 0, "reconciled": True, **rebuilt}
        try:
            diagnostics = self.rebuild()
        except Exception as exc:
            self.repository.mark_jobs(job_ids, error=str(exc))
            raise
        self.repository.mark_jobs(job_ids)
        return {"processed": len(job_ids), **diagnostics}

    def search(self, query: str, taxonomy_filters: dict[str, str], limit: int) -> dict:
        """先从 MySQL 生成允许 ID，再在 hnswlib 中搜索并复核当前正文。"""

        started = perf_counter()
        allowed_ids = self.repository.allowed_ids(taxonomy_filters)
        matches = self.index.search(query, allowed_ids, limit)
        current = self.repository.allowed_documents(
            [int(item["skill_id"]) for item in matches], taxonomy_filters
        )
        items = [
            {**current[int(item["skill_id"])], "score": item["score"]}
            for item in matches
            if int(item["skill_id"]) in current
        ]
        diagnostics = self.index.diagnostics
        return {
            "items": items,
            "latency_ms": int((perf_counter() - started) * 1000),
            "embedding_model": diagnostics["embedding_model"],
            "index_version": diagnostics["index_version"],
        }

    @property
    def diagnostics(self) -> dict:
        """返回不含正文的索引状态及 outbox 积压诊断。"""

        counts = self.repository.job_counts()
        return {
            **self.index.diagnostics,
            "pending_jobs": counts.get("pending", 0),
            "failed_jobs": counts.get("failed", 0),
        }

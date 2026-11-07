"""轻量 Skill 派生索引的数据库仓储。"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, or_, select

from backend.database.models import Skill, SkillIndexJob
from backend.database.session import get_session_factory
from backend.schemas.skill_taxonomy import normalize_taxonomy_path, taxonomy_matches


class SkillIndexRepository:
    """读取可索引 Skill，并维护单进程消费的 outbox 状态。"""

    def __init__(self, session_factory=None):
        """注入统一 Session 工厂，测试可使用 SQLite。"""

        self.session_factory = session_factory or get_session_factory()

    def active_documents(self) -> list[dict]:
        """返回全部可进入客服回答的已发布 Skill。"""

        with self.session_factory() as session:
            rows = list(session.scalars(select(Skill).where(
                Skill.status == "published",
                or_(Skill.expires_at.is_(None), Skill.expires_at > func.now()),
            ).order_by(Skill.id.asc())))
        result = []
        for row in rows:
            try:
                path = normalize_taxonomy_path(row.taxonomy_path, require_publishable=True)
            except ValueError:
                continue
            if path[0]["code"] == "review_policy":
                continue
            result.append({
                "skill_id": row.id,
                "title": row.title,
                "content": row.content,
                "taxonomy_path": path,
                "version": int(row.version or 1),
            })
        return result

    def allowed_documents(
        self, skill_ids: list[int], taxonomy_filters: dict[str, str]
    ) -> dict[int, dict]:
        """按当前数据库状态和硬分类范围加载向量候选正文。"""

        if not skill_ids:
            return {}
        with self.session_factory() as session:
            rows = list(session.scalars(select(Skill).where(
                Skill.id.in_(skill_ids),
                Skill.status == "published",
                or_(Skill.expires_at.is_(None), Skill.expires_at > func.now()),
            )))
        result = {}
        for row in rows:
            try:
                path = normalize_taxonomy_path(row.taxonomy_path, require_publishable=True)
            except ValueError:
                continue
            if path[0]["code"] == "review_policy" or not taxonomy_matches(path, taxonomy_filters):
                continue
            result[row.id] = {
                "skill_id": row.id,
                "title": row.title,
                "content": row.content,
                "taxonomy_path": path,
            }
        return result

    def allowed_ids(self, taxonomy_filters: dict[str, str]) -> set[int]:
        """先按数据库硬边界生成 hnswlib 允许标签集合。

        在线查询只读取 ID 与分类路径，不加载全部标题和正文；最终 Top20 仍会
        通过 ``allowed_documents`` 再次读取 MySQL 当前正文并复核状态。
        """

        with self.session_factory() as session:
            rows = list(session.execute(
                select(Skill.id, Skill.taxonomy_path).where(
                    Skill.status == "published",
                    or_(Skill.expires_at.is_(None), Skill.expires_at > func.now()),
                )
            ))
        result: set[int] = set()
        for skill_id, raw_path in rows:
            try:
                path = normalize_taxonomy_path(raw_path, require_publishable=True)
            except ValueError:
                continue
            if path[0]["code"] == "review_policy" or not taxonomy_matches(path, taxonomy_filters):
                continue
            result.add(int(skill_id))
        return result

    def active_count(self) -> int:
        """返回当前应存在于派生索引中的有效 Skill 数量。

        后台循环用该轻量计数发现自然到期或首次部署场景；只有数量变化时才
        全量重建，避免固定周期重复执行 Embedding。
        """

        with self.session_factory() as session:
            dialect = session.get_bind().dialect.name
            conditions = [
                Skill.status == "published",
                or_(Skill.expires_at.is_(None), Skill.expires_at > func.now()),
            ]
            if dialect == "mysql":
                conditions.append(~func.json_contains(
                    Skill.taxonomy_path,
                    func.json_object(
                        "level", "knowledge_domain", "code", "review_policy"
                    ),
                ))
                return int(session.scalar(select(func.count(Skill.id)).where(*conditions)) or 0)
            rows = list(session.scalars(select(Skill).where(*conditions)))
        count = 0
        for row in rows:
            try:
                path = normalize_taxonomy_path(row.taxonomy_path, require_publishable=True)
            except ValueError:
                continue
            if path[0]["code"] != "review_policy":
                count += 1
        return count

    def pending_job_ids(self, limit: int = 100) -> list[int]:
        """读取待处理 outbox ID；只有一个索引进程消费，无需并行抢占。"""

        with self.session_factory() as session:
            return list(session.scalars(
                select(SkillIndexJob.id)
                .where(SkillIndexJob.status == "pending")
                .order_by(SkillIndexJob.id.asc())
                .limit(max(1, min(limit, 1000)))
            ))

    def job_counts(self) -> dict[str, int]:
        """按状态汇总 outbox 数量，供本机健康检查暴露积压和失败。"""

        with self.session_factory() as session:
            rows = list(session.execute(
                select(SkillIndexJob.status, func.count(SkillIndexJob.id))
                .group_by(SkillIndexJob.status)
            ))
        return {str(status): int(count) for status, count in rows}

    def mark_jobs(self, job_ids: list[int], *, error: str = "") -> None:
        """把一批 outbox 标记为成功或失败，失败任务保留重试。"""

        if not job_ids:
            return
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with self.session_factory.begin() as session:
            rows = list(session.scalars(
                select(SkillIndexJob).where(SkillIndexJob.id.in_(job_ids)).with_for_update()
            ))
            for row in rows:
                row.attempts = int(row.attempts or 0) + 1
                row.last_error = error[:4000]
                if error:
                    row.status = "pending" if row.attempts < 5 else "failed"
                else:
                    row.status = "succeeded"
                    row.processed_at = now

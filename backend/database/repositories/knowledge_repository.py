"""已发布 Skill 的知识查询仓储。"""

from __future__ import annotations

from time import perf_counter
from typing import Any

from sqlalchemy import func, or_, select

from backend.database.models import Skill
from backend.database.session import get_session_factory
from backend.schemas.ai import ConversationContext, KnowledgeReference
from backend.schemas.identifiers import search_terms
from backend.schemas.skill_taxonomy import normalize_taxonomy_path, taxonomy_matches


class PublishedSkillKnowledgeRepository:
    """从 MySQL 查询已发布 Skill，并执行轻量关键词召回。"""

    supports_taxonomy_filters = True

    def __init__(self, session_factory=None, vector_client=None, strategy_version: str = "lexical-v1"):
        """初始化仓储。

        Args:
            session_factory: 可选 SQLAlchemy Session 工厂，测试时可注入替身。
        """
        self.session_factory = session_factory or get_session_factory()
        self.vector_client = vector_client
        self.strategy_version = strategy_version

    @staticmethod
    def _path_conditions(taxonomy_filters: dict[str, str] | None) -> list[Any]:
        """把硬分类范围转换为 MySQL JSON 条件，禁止向量或全文跨分支。"""

        conditions: list[Any] = []
        for level, value in (taxonomy_filters or {}).items():
            normalized = str(value or "").strip()
            if not normalized:
                continue
            conditions.append(or_(
                func.json_contains(
                    Skill.taxonomy_path,
                    func.json_object("level", level, "code", normalized),
                ),
                func.json_contains(
                    Skill.taxonomy_path,
                    func.json_object("level", level, "name", normalized),
                ),
            ))
        return conditions

    def _legacy_lexical(
        self, terms: set[str], taxonomy_filters: dict[str, str] | None
    ) -> list[tuple[float, Skill, list[dict[str, str]]]]:
        """为 SQLite 测试和 FULLTEXT 异常保留有边界的兼容召回。"""

        with self.session_factory() as session:
            rows = list(session.scalars(
                select(Skill).where(
                    Skill.status == "published",
                    (Skill.expires_at.is_(None)) | (Skill.expires_at > func.now()),
                ).order_by(Skill.id.desc())
            ))
        ranked = []
        for row in rows:
            try:
                path = normalize_taxonomy_path(row.taxonomy_path, require_publishable=True)
            except ValueError:
                continue
            if path and path[0].get("code") == "review_policy":
                continue
            if taxonomy_filters and not taxonomy_matches(path, taxonomy_filters):
                continue
            score = 4 * len(terms & search_terms(row.title)) + len(terms & search_terms(row.content))
            if score:
                ranked.append((float(score), row, path))
        ranked.sort(key=lambda pair: (-pair[0], -pair[1].id))
        return ranked[:20]

    def _lexical_candidates(
        self, query: str, terms: set[str], taxonomy_filters: dict[str, str] | None
    ) -> tuple[list[tuple[float, Skill, list[dict[str, str]]]], int, str]:
        """优先执行 MySQL FULLTEXT；不可用时回退现有关键词算法。"""

        started = perf_counter()
        fallback = ""
        with self.session_factory() as session:
            dialect = session.get_bind().dialect.name
        if dialect != "mysql":
            rows = self._legacy_lexical(terms, taxonomy_filters)
            return rows, int((perf_counter() - started) * 1000), "非 MySQL 环境使用关键词回退"
        search_query = " ".join(sorted(terms))
        try:
            title_score = Skill.title.match(search_query, mysql_boolean_mode=True)
            content_score = Skill.content.match(search_query, mysql_boolean_mode=True)
            score = (title_score * 4 + content_score).label("relevance")
            conditions = [
                Skill.status == "published",
                (Skill.expires_at.is_(None)) | (Skill.expires_at > func.now()),
                score > 0,
                ~func.json_contains(
                    Skill.taxonomy_path,
                    func.json_object("level", "knowledge_domain", "code", "review_policy"),
                ),
                *self._path_conditions(taxonomy_filters),
            ]
            with self.session_factory() as session:
                raw_rows = list(session.execute(
                    select(Skill, score)
                    .where(*conditions)
                    .order_by(score.desc(), Skill.id.desc())
                    .limit(20)
                ))
            rows = []
            for row, relevance in raw_rows:
                try:
                    path = normalize_taxonomy_path(row.taxonomy_path, require_publishable=True)
                except ValueError:
                    continue
                rows.append((float(relevance or 0), row, path))
        except Exception as exc:  # FULLTEXT 索引或数据库能力异常必须无感降级。
            fallback = f"FULLTEXT 回退关键词算法：{type(exc).__name__}"
            rows = self._legacy_lexical(terms, taxonomy_filters)
        return rows, int((perf_counter() - started) * 1000), fallback

    def search_with_trace(
        self,
        context: ConversationContext,
        query: str,
        limit: int = 5,
        taxonomy_filters: dict[str, str] | None = None,
    ) -> tuple[list[KnowledgeReference], list[dict[str, Any]]]:
        """在已发布 Skill 中执行混合召回，并返回完整候选轨迹。

        Args:
            context: 当前会话上下文，预留给后续产品或客户范围过滤。
            query: 从客户最新问题中提取的检索文本。
            limit: 最多返回的知识数量，内部限制在 1 到 10。
            taxonomy_filters: 意图阶段确定的知识域和产品层级，不允许跨分支召回。

        Returns:
            最终知识引用和全文/向量/RRF 候选轨迹。轨迹保留 Top20 召回，
            最终引用仍受 ``limit`` 限制，避免为了审计扩大模型上下文。
        """
        del context
        terms = search_terms(query)
        if not terms:
            return [], []
        lexical, lexical_ms, lexical_fallback = self._lexical_candidates(
            query, terms, taxonomy_filters
        )
        candidates: dict[str, KnowledgeReference] = {}
        scores: dict[str, float] = {}
        details: dict[str, dict[str, Any]] = {}
        for rank, (raw_score, row, path) in enumerate(lexical, start=1):
            ref_id = f"skill:{row.id}"
            candidates[ref_id] = KnowledgeReference(
                reference_id=ref_id,
                title=row.title,
                content=row.content,
                product_model=next((item["name"] for item in path if item["level"] == "product_model"), ""),
                metadata={"skill_id": str(row.id), "review_status": "published", "taxonomy_path": path},
            )
            scores[ref_id] = scores.get(ref_id, 0.0) + 1.0 / (60 + rank)
            details.setdefault(ref_id, {}).update({"lexical_rank": rank, "lexical_score": raw_score})

        vector_diagnostics = {"fallback_reason": "", "embedding_model": "", "index_version": "", "vector_ms": 0}
        if self.vector_client is not None:
            vector_rows, vector_diagnostics = self.vector_client.search(query, taxonomy_filters or {}, 20)
            for rank, item in enumerate(vector_rows, start=1):
                try:
                    skill_id = int(item["skill_id"])
                except (KeyError, TypeError, ValueError):
                    continue
                ref_id = f"skill:{skill_id}"
                path = item.get("taxonomy_path") or []
                if not taxonomy_matches(path, taxonomy_filters or {}):
                    continue
                if path and path[0].get("code") == "review_policy":
                    continue
                candidates.setdefault(ref_id, KnowledgeReference(
                    reference_id=ref_id,
                    title=str(item.get("title") or ""),
                    content=str(item.get("content") or ""),
                    product_model=next((part.get("name", "") for part in path if part.get("level") == "product_model"), ""),
                    metadata={"skill_id": str(skill_id), "review_status": "published", "taxonomy_path": path},
                ))
                scores[ref_id] = scores.get(ref_id, 0.0) + 1.0 / (60 + rank)
                details.setdefault(ref_id, {}).update({"vector_rank": rank, "vector_score": float(item.get("score") or 0)})

        # RRF 分数相同时使用稳定 Skill ID 排序，确保同一索引版本可复现。
        ordered = sorted(candidates, key=lambda item: (-scores[item], int(item.split(":", 1)[1])))
        fallback_reason = "；".join(
            item
            for item in (
                lexical_fallback,
                str(vector_diagnostics.get("fallback_reason") or ""),
            )
            if item
        )
        trace_candidates: list[dict[str, Any]] = []
        for rule_rank, ref_id in enumerate(ordered, start=1):
            item = candidates[ref_id]
            retrieval = {
                **details[ref_id],
                "final_rank": rule_rank,
                "rrf_score": round(scores[ref_id], 8),
                "strategy_version": self.strategy_version,
                "embedding_model": str(vector_diagnostics.get("embedding_model") or ""),
                "index_version": str(vector_diagnostics.get("index_version") or ""),
                "lexical_ms": lexical_ms,
                "vector_ms": int(vector_diagnostics.get("vector_ms") or 0),
                "fallback_reason": fallback_reason,
            }
            trace_candidates.append({
                "source_ref": ref_id,
                "source_type": "skill",
                "final_rank": rule_rank,
                "title": item.title,
                "retrieval": retrieval,
            })

        result = []
        for final_rank, ref_id in enumerate(ordered[: max(1, min(limit, 10))], start=1):
            item = candidates[ref_id]
            retrieval = dict(trace_candidates[final_rank - 1]["retrieval"])
            retrieval["final_rank"] = final_rank
            result.append(KnowledgeReference(
                reference_id=item.reference_id,
                title=item.title,
                content=item.content,
                product_model=item.product_model,
                metadata={
                    **item.metadata,
                    "retrieval": retrieval,
                },
            ))
        return result, trace_candidates

    def search(
        self,
        context: ConversationContext,
        query: str,
        limit: int = 5,
        taxonomy_filters: dict[str, str] | None = None,
    ) -> list[KnowledgeReference]:
        """兼容统一知识协议，只返回最终引用并丢弃内部审计轨迹。"""

        references, _ = self.search_with_trace(
            context, query, limit=limit, taxonomy_filters=taxonomy_filters
        )
        return references

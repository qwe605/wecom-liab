"""知识检索业务编排。具体文件和数据库实现位于适配层。"""

from __future__ import annotations

from typing import Protocol

from backend.config import Settings
from backend.schemas.ai import ConversationContext, KnowledgeReference
from backend.schemas.identifiers import search_terms


class KnowledgeProvider(Protocol):
    """所有知识来源必须实现的稳定检索协议。"""

    def search(
        self,
        context: ConversationContext,
        query: str,
        limit: int = 5,
        taxonomy_filters: dict[str, str] | None = None,
    ) -> list[KnowledgeReference]:
        """根据当前会话问题返回不超过 limit 条统一知识引用。"""
        ...


class CompositeKnowledgeProvider:
    """聚合数据库与语雀 MCP 知识来源，并按引用编号去重。"""

    def __init__(self, providers: list[KnowledgeProvider]):
        """保存按优先级排列的知识提供者列表。"""
        self.providers = providers

    def search(self, context: ConversationContext, query: str, limit: int = 5, taxonomy_filters: dict[str, str] | None = None) -> list[KnowledgeReference]:
        """依次检索各知识源，传递分类范围并按引用编号去重。"""
        result, seen = [], set()
        for provider in self.providers:
            for item in provider.search(context, query, limit, taxonomy_filters):
                if item.reference_id not in seen:
                    seen.add(item.reference_id)
                    result.append(item)
        return result[: max(1, min(limit, 10))]

    def search_route(
        self,
        context: ConversationContext,
        query: str,
        taxonomy_filters: dict[str, str],
        domains: tuple[str, ...],
        limit: int = 5,
    ) -> tuple[list[KnowledgeReference], list[str]]:
        """主域优先检索，并保证不识别分类的外部知识源只调用一次。"""

        references, searched_domains, _ = self.search_route_with_trace(
            context, query, taxonomy_filters, domains, limit
        )
        return references, searched_domains

    def search_route_with_trace(
        self,
        context: ConversationContext,
        query: str,
        taxonomy_filters: dict[str, str],
        domains: tuple[str, ...],
        limit: int = 5,
    ) -> tuple[list[KnowledgeReference], list[str], list[dict]]:
        """执行主域优先检索，并汇总所有实际召回候选的内部轨迹。

        不支持轨迹协议的外部知识源仍按稳定 ``search`` 协议调用，并只为其
        返回结果生成最小轨迹；内部 Skill 则保留全文与向量 Top20 明细。
        """

        result: list[KnowledgeReference] = []
        seen: set[str] = set()
        searched_domains: list[str] = []
        trace_candidates: list[dict] = []
        bounded_limit = max(1, min(limit, 10))
        for index, domain in enumerate(domains):
            searched_domains.append(domain)
            domain_filters = {**taxonomy_filters, "knowledge_domain": domain}
            before_count = len(result)
            for provider in self.providers:
                # 语雀等知识源不使用 Skill 分类路径，一次回答中只在主域阶段查询一次。
                if index > 0 and not getattr(provider, "supports_taxonomy_filters", False):
                    continue
                traced_search = getattr(provider, "search_with_trace", None)
                if callable(traced_search):
                    provider_items, provider_trace = traced_search(
                        context, query, bounded_limit, domain_filters
                    )
                else:
                    provider_items = provider.search(
                        context, query, bounded_limit, domain_filters
                    )
                    provider_trace = [
                        {
                            "source_ref": item.reference_id,
                            "source_type": (
                                "skill" if item.reference_id.startswith("skill:") else "yuque"
                            ),
                            "final_rank": rank,
                            "title": item.title,
                            "retrieval": (
                                item.metadata.get("retrieval", {})
                                if isinstance(item.metadata, dict)
                                else {}
                            ),
                        }
                        for rank, item in enumerate(provider_items, start=1)
                    ]
                for candidate in provider_trace:
                    if isinstance(candidate, dict):
                        trace_candidates.append({**candidate, "knowledge_domain": domain})
                for item in provider_items:
                    if item.reference_id not in seen:
                        seen.add(item.reference_id)
                        result.append(item)
            if index == 0 and len(result) > before_count:
                break
            if len(result) >= bounded_limit:
                break
        return result[:bounded_limit], searched_domains, trace_candidates


def build_knowledge_provider(settings: Settings) -> CompositeKnowledgeProvider:
    """使用集中配置装配知识来源。"""
    from backend.database.repositories.knowledge_repository import PublishedSkillKnowledgeRepository
    from backend.integrations.lightweight_vector import LocalVectorSearchClient
    from backend.integrations.yuque_mcp import YuqueMcpKnowledgeProvider

    vector_client = None
    if getattr(settings, "lightweight_search_enabled", False):
        vector_client = LocalVectorSearchClient(
            settings.lightweight_search_url,
            settings.lightweight_search_timeout_seconds,
        )
    return CompositeKnowledgeProvider([
        PublishedSkillKnowledgeRepository(
            vector_client=vector_client,
            strategy_version=getattr(settings, "retrieval_strategy_version", "lexical-v1"),
        ),
        YuqueMcpKnowledgeProvider(
            mcp_url=settings.yuque_mcp_url,
            timeout_seconds=settings.yuque_mcp_timeout_seconds,
            product_index_repo=settings.yuque_product_index_repo,
            product_index_doc=settings.yuque_product_index_doc,
            product_docs_repo=settings.yuque_product_docs_repo,
        ),
    ])

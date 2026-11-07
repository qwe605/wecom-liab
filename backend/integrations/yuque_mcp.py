"""通过 Streamable HTTP MCP 查询语雀产品知识库。"""

from __future__ import annotations

import asyncio
from datetime import timedelta
import json
import re
import time
from typing import Any, Protocol

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from backend.schemas.ai import ConversationContext, KnowledgeReference
from backend.schemas.identifiers import search_terms


PRODUCT_MODEL_PATTERN = re.compile(
    r"\b[A-Z]{1,10}[-_]?[A-Z0-9]*\d[A-Z0-9._-]*\b",
    re.IGNORECASE,
)
CONFIGURATION_TERMS = {
    "支持",
    "能否",
    "能不能",
    "默认",
    "配置",
    "设置",
    "参数",
    "规格",
    "连接",
    "校准",
    "故障",
}
DETAIL_DOC_TERMS = {"faq", "常见问题", "规格", "参数", "说明书", "使用手册"}


class McpToolCaller(Protocol):
    """语雀知识提供者依赖的最小 MCP 工具调用协议。"""

    def call(self, name: str, arguments: dict[str, Any]) -> Any:
        """调用一个 MCP 工具并返回其结构化结果。"""


class StreamableHttpMcpToolCaller:
    """使用官方 MCP Python SDK 调用 Streamable HTTP 服务。"""

    def __init__(self, mcp_url: str, timeout_seconds: int):
        """保存 MCP 地址与单次工具调用超时。"""

        self.mcp_url = mcp_url.rstrip("/")
        self.timeout_seconds = max(int(timeout_seconds), 3)

    def call(self, name: str, arguments: dict[str, Any]) -> Any:
        """在同步业务线程中完成一次 MCP 会话和工具调用。

        Raises:
            RuntimeError: MCP 服务不可达、工具报错或没有返回可解析结构。
        """

        try:
            return asyncio.run(self._call(name, arguments))
        except Exception as exc:
            raise RuntimeError(f"语雀 MCP 工具 {name} 调用失败: {exc}") from exc

    async def _call(self, name: str, arguments: dict[str, Any]) -> Any:
        """建立短生命周期 MCP 会话并解析工具结果。"""

        timeout = httpx.Timeout(float(self.timeout_seconds))
        async with httpx.AsyncClient(timeout=timeout) as http_client:
            async with streamable_http_client(
                self.mcp_url,
                http_client=http_client,
            ) as (read_stream, write_stream, _):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.call_tool(
                        name,
                        arguments,
                        read_timeout_seconds=timedelta(seconds=self.timeout_seconds),
                    )
        if result.isError:
            message = "；".join(
                str(getattr(item, "text", "") or "") for item in result.content
            ).strip()
            raise RuntimeError(message or "MCP 工具返回错误")
        if result.structuredContent is not None:
            return result.structuredContent
        for item in result.content:
            text = str(getattr(item, "text", "") or "").strip()
            if not text:
                continue
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return text
        raise RuntimeError("MCP 工具没有返回内容")


class YuqueMcpKnowledgeProvider:
    """按产品型号路由语雀文档，并统一转换为知识引用。"""

    def __init__(
        self,
        mcp_url: str,
        timeout_seconds: int,
        product_index_repo: str,
        product_index_doc: str,
        product_docs_repo: str,
        caller: McpToolCaller | None = None,
        cache_seconds: int = 300,
    ):
        """配置 MCP、产品汇总文档和短时只读缓存。

        Args:
            mcp_url: Streamable HTTP MCP 端点，通常为本机 ``/mcp``。
            timeout_seconds: 单次 MCP 工具调用超时。
            product_index_repo: 产品汇总文档所属知识库。
            product_index_doc: 产品汇总文档 ID 或 slug。
            product_docs_repo: 产品详细资料知识库。
            caller: 测试可注入的 MCP 工具调用器。
            cache_seconds: 产品目录和文档的进程内缓存时间。
        """

        if not mcp_url.strip():
            raise ValueError("YUQUE_MCP_URL 不能为空")
        if not all(value.strip() for value in (product_index_repo, product_index_doc, product_docs_repo)):
            raise ValueError("请配置授权语雀知识库及产品汇总文档，不使用任何真实企业默认值")
        self.caller = caller or StreamableHttpMcpToolCaller(
            mcp_url,
            timeout_seconds,
        )
        self.product_index_repo = product_index_repo
        self.product_index_doc = product_index_doc
        self.product_docs_repo = product_docs_repo
        self.cache_seconds = max(int(cache_seconds), 0)
        self._cache: dict[tuple[str, str, str], tuple[float, Any]] = {}

    def search(
        self,
        context: ConversationContext,
        query: str,
        limit: int = 5,
        taxonomy_filters: dict[str, str] | None = None,
    ) -> list[KnowledgeReference]:
        """优先读取锁定型号资料，未匹配时回退语雀全文搜索。

        Raises:
            RuntimeError: 语雀 MCP 不可用或返回无效数据。
        """

        del context
        safe_limit = max(1, min(int(limit), 10))
        # 语雀资料按专业目录检索：先把已确认的目录节点加入查询词，避免
        # 全库搜索跨产品分支；taxonomy_filters 只来自结构化意图和审核路径。
        scope_terms = [
            str(value).strip()
            for level, value in (taxonomy_filters or {}).items()
            if level in {"product_category", "product_subcategory", "product_model", "topic"}
            and str(value).strip()
        ]
        scoped_query = " ".join(dict.fromkeys([query.strip(), *scope_terms])).strip()
        product_model = self._product_model(scoped_query)
        if product_model:
            matched = self._search_product(product_model, scoped_query, safe_limit)
            if matched:
                return matched
        return self._search_documents(scoped_query, product_model, safe_limit)

    def _search_product(
        self,
        product_model: str,
        query: str,
        limit: int,
    ) -> list[KnowledgeReference]:
        """从产品汇总文档定位目标型号，并按需补充 FAQ 与规格书。"""

        index_document = self._cached_call(
            "yuque_get_doc",
            {
                "repo_id": self.product_index_repo,
                "doc_id": self.product_index_doc,
            },
        )
        target = self._product_target_from_index(
            str(self._dict(index_document).get("body") or ""),
            product_model,
        )
        if target is None:
            return []
        repo_id, doc_id = target
        if not doc_id:
            return []
        document = self._cached_call(
            "yuque_get_doc",
            {"repo_id": repo_id, "doc_id": doc_id},
        )
        references = [self._document_reference(document, repo_id, doc_id, query, product_model)]
        body = str(self._dict(document).get("body") or "")
        if self._needs_detail_docs(query, body):
            references.extend(
                self._detail_references(repo_id, doc_id, query, product_model, limit - 1)
            )
        return self._deduplicate(references, limit)

    def _detail_references(
        self,
        repo_id: str,
        doc_id: str,
        query: str,
        product_model: str,
        limit: int,
    ) -> list[KnowledgeReference]:
        """读取产品索引页引用的 FAQ、规格书和与问题最相关的子文档。"""

        if limit <= 0:
            return []
        info = self._cached_call(
            "yuque_get_sub_doc",
            {"repo_id": repo_id, "doc_id": doc_id},
        )
        query_terms = search_terms(query)
        ranked: list[tuple[int, dict[str, Any]]] = []
        for item in self._dict(info).get("sub_docs") or []:
            if not isinstance(item, dict):
                continue
            text = f"{item.get('doc_name', '')} {item.get('doc_desc', '')}".casefold()
            score = 10 if any(term in text for term in DETAIL_DOC_TERMS) else 0
            score += len(query_terms & search_terms(text))
            if score:
                ranked.append((score, item))
        ranked.sort(key=lambda pair: -pair[0])
        references = []
        for _, item in ranked[:limit]:
            child_repo = str(item.get("repo_id") or repo_id)
            child_id = str(item.get("doc_id") or "")
            if not child_id:
                continue
            document = self._cached_call(
                "yuque_get_doc",
                {"repo_id": child_repo, "doc_id": child_id},
            )
            references.append(
                self._document_reference(
                    document,
                    child_repo,
                    child_id,
                    query,
                    product_model,
                )
            )
        return references

    def _search_documents(
        self,
        query: str,
        product_model: str,
        limit: int,
    ) -> list[KnowledgeReference]:
        """使用语雀全文搜索结果构造知识引用。"""

        payload = self.caller.call("yuque_search", {"query": query, "type": "doc"})
        references = []
        for item in self._dict(payload).get("items") or []:
            if not isinstance(item, dict):
                continue
            book = item.get("book") if isinstance(item.get("book"), dict) else {}
            repo_id = str(book.get("namespace") or self.product_docs_repo)
            source_url = str(item.get("url") or "")
            doc_id = self._doc_id_from_url(source_url) or str(item.get("id") or "")
            body = str(item.get("body") or "")
            if not body and doc_id:
                document = self._cached_call(
                    "yuque_get_doc",
                    {"repo_id": repo_id, "doc_id": doc_id},
                )
                references.append(
                    self._document_reference(
                        document,
                        repo_id,
                        doc_id,
                        query,
                        product_model,
                    )
                )
                continue
            references.append(
                KnowledgeReference(
                    reference_id=f"yuque:{repo_id}:{doc_id}",
                    title=str(item.get("title") or "语雀文档"),
                    content=self._excerpt(body, query),
                    source_url=source_url,
                    product_model=product_model,
                    metadata={"source": "yuque_mcp", "repo_id": repo_id, "doc_id": doc_id},
                )
            )
        return self._deduplicate(references, limit)

    def _document_reference(
        self,
        payload: Any,
        repo_id: str,
        doc_id: str,
        query: str,
        product_model: str,
    ) -> KnowledgeReference:
        """把 ``yuque_get_doc`` 结果转换成稳定知识引用。"""

        document = self._dict(payload)
        slug = str(document.get("slug") or doc_id)
        return KnowledgeReference(
            reference_id=f"yuque:{repo_id}:{doc_id}",
            title=str(document.get("title") or "语雀文档"),
            content=self._excerpt(str(document.get("body") or ""), query),
            source_url=f"https://www.yuque.com/{repo_id}/{slug}",
            product_model=product_model,
            metadata={"source": "yuque_mcp", "repo_id": repo_id, "doc_id": doc_id},
        )

    def _cached_call(self, name: str, arguments: dict[str, Any]) -> Any:
        """在短时缓存内复用只读 MCP 工具结果，降低回复延迟和语雀请求量。"""

        key = (name, str(arguments.get("repo_id") or ""), str(arguments.get("doc_id") or ""))
        cached = self._cache.get(key)
        now = time.monotonic()
        if cached is not None and now - cached[0] <= self.cache_seconds:
            return cached[1]
        result = self.caller.call(name, arguments)
        self._cache[key] = (now, result)
        return result

    @staticmethod
    def _dict(payload: Any) -> dict[str, Any]:
        """兼容 FastMCP 结构化结果外层的 ``result`` 包装。"""

        if not isinstance(payload, dict):
            raise RuntimeError("语雀 MCP 返回结果不是对象")
        result = payload.get("result")
        return result if isinstance(result, dict) else payload

    @staticmethod
    def _product_model(query: str) -> str:
        """从结构化检索词中提取最可能的产品型号。"""

        match = PRODUCT_MODEL_PATTERN.search(query.upper())
        return match.group(0).upper() if match else ""

    def _product_target_from_index(
        self,
        body: str,
        product_model: str,
    ) -> tuple[str, str] | None:
        """从产品汇总表中包含目标型号的行提取产品资料知识库链接。"""

        model_folded = product_model.casefold()
        link_pattern = re.compile(
            r"https?://[a-zA-Z0-9-]+\.yuque\.com/"
            r"([a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+)/([a-zA-Z0-9_-]+)"
        )
        candidates: list[tuple[str, str]] = []
        for line in body.splitlines():
            if model_folded not in line.casefold():
                continue
            candidates.extend(link_pattern.findall(line))
        if not candidates:
            return None
        return next(
            (item for item in candidates if item[0] == self.product_docs_repo),
            candidates[0],
        )

    @staticmethod
    def _doc_id_from_url(source_url: str) -> str:
        """从语雀搜索结果 URL 中提取可用于 ``yuque_get_doc`` 的 slug。"""

        match = re.search(r"/([a-zA-Z0-9_-]+)(?:[?#].*)?$", source_url)
        return match.group(1) if match else ""

    @staticmethod
    def _needs_detail_docs(query: str, body: str) -> bool:
        """判断问题是否必须继续查 FAQ/规格书，或当前文档是否只是短索引页。"""

        return len(body.strip()) < 800 or any(term in query for term in CONFIGURATION_TERMS)

    @staticmethod
    def _excerpt(body: str, query: str, max_chars: int = 7000) -> str:
        """截取包含检索词的原文窗口，并保留 Markdown 图片链接。"""

        text = body.strip()
        if len(text) <= max_chars:
            return text
        positions = []
        folded = text.casefold()
        for term in sorted(search_terms(query), key=len, reverse=True):
            position = folded.find(term.casefold())
            if position >= 0:
                positions.append(position)
        if not positions:
            return text[:max_chars]
        chunks = []
        per_chunk = max(1200, max_chars // min(len(positions), 4))
        for position in positions[:4]:
            start = max(position - per_chunk // 3, 0)
            chunks.append(text[start : start + per_chunk])
        return "\n\n---\n\n".join(chunks)[:max_chars]

    @staticmethod
    def _deduplicate(
        references: list[KnowledgeReference],
        limit: int,
    ) -> list[KnowledgeReference]:
        """按引用编号去重并限制返回数量。"""

        result = []
        seen = set()
        for item in references:
            if item.reference_id in seen or not item.content.strip():
                continue
            seen.add(item.reference_id)
            result.append(item)
        return result[:limit]

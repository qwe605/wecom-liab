"""基于官方 MCP SDK 的最小语雀只读服务。

仅注册应用所需的三种查询工具；不包含发布、编辑、删除、成员管理等写操作。
凭据仅从环境读取，知识库必须显式授权；默认只监听回环地址。
"""

from __future__ import annotations

import os
import re
from urllib.parse import quote, urlsplit

import httpx
from mcp.server.fastmcp import FastMCP


class ReadonlyYuqueClient:
    """限制请求范围的语雀读取适配器，不持久化任何真实文档。"""

    def __init__(self, token: str, repositories: set[str]):
        """保存最小权限 Token 和授权知识库；缺少配置时拒绝启动。"""

        if not token.strip() or not repositories:
            raise ValueError("请配置语雀只读 Token 和授权知识库")
        if any(not re.fullmatch(r"[\w-]+/[\w-]+", repo) for repo in repositories):
            raise ValueError("知识库必须使用 team/repository 格式")
        self._token = token
        self.repositories = frozenset(repositories)

    def _get(self, path: str, params: dict | None = None):
        """只向官方 HTTPS API 发起 GET；异常不包含响应正文或请求凭据。"""

        try:
            with httpx.Client(timeout=15, follow_redirects=False) as client:
                response = client.get(
                    f"https://www.yuque.com/api/v2/{path}",
                    params=params,
                    headers={"X-Auth-Token": self._token, "User-Agent": "wecom-liab-readonly"},
                )
                response.raise_for_status()
                return response.json()["data"]
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise RuntimeError("语雀读取失败，请通过受控运维渠道核验连接和权限") from None

    def document(self, repo_id: str, doc_id: str) -> dict:
        """读取授权库内单篇文档；返回上层协议需要的最小字段。"""

        if repo_id not in self.repositories:
            raise PermissionError("知识库不在授权范围内")
        if not re.fullmatch(r"[\w-]+", doc_id):
            raise ValueError("文档 ID 格式错误")
        doc = self._get(f"repos/{quote(repo_id, safe='/')}/docs/{quote(doc_id, safe='')}")
        return {
            "id": doc.get("id"), "slug": doc.get("slug") or doc_id,
            "title": doc.get("title") or "", "body": doc.get("body") or "",
            "book": {"namespace": repo_id},
        }

    def subdocuments(self, repo_id: str, doc_id: str) -> dict:
        """解析文档中的语雀引用；只返回授权库的链接，不自动读取其他库。"""

        doc = self.document(repo_id, doc_id)
        items = []
        seen = set()
        pattern = re.compile(r"https://[\w-]+\.yuque\.com/([\w-]+/[\w-]+)/([\w-]+)")
        for line in doc["body"].splitlines():
            for match in pattern.finditer(line):
                repo, slug = match.groups()
                if repo not in self.repositories or (repo, slug) in seen:
                    continue
                seen.add((repo, slug))
                # 使用链接前的行内描述保留 FAQ/规格书路由信息，不注入企业资料。
                title = line[:match.start()].strip(" |`[]()") or slug
                items.append({
                    "repo_id": repo, "doc_id": slug, "doc_name": title,
                    "doc_desc": "", "doc_url": match.group(0),
                })
        return {"sub_docs": items}

    def search(self, query: str, kind: str = "doc") -> dict:
        """搜索文档并在返回前过滤知识库；禁止查询用户或其他资源类型。"""

        if kind != "doc" or not query.strip() or len(query) > 4000:
            raise ValueError("只允许 1 至 4000 字符的文档查询")
        data = self._get("search", {"q": query, "type": "doc"})
        rows = data if isinstance(data, list) else data.get("items", [])
        items = []
        for row in rows:
            repo = (row.get("book") or {}).get("namespace", "")
            if repo not in self.repositories:
                continue
            # 不向业务层透传用户资料、成员信息等搜索结果的附带字段。
            url = str(row.get("url") or "")
            parsed = urlsplit(url)
            host = parsed.hostname or ""
            if parsed.scheme != "https" or not host.endswith(".yuque.com"):
                url = ""
            items.append({
                "id": row.get("id"), "title": row.get("title") or "",
                "body": row.get("body") or "", "url": url,
                "book": {"namespace": repo},
            })
        return {"items": items}


def create_mcp() -> FastMCP:
    """从环境构造只读服务；保留官方 SDK 默认的 DNS 重绑定防护。"""

    token = os.environ.get("YUQUE_PERSONAL_TOKEN", "")
    repositories = {
        value.strip() for value in (
            os.environ.get("YUQUE_PRODUCT_INDEX_REPO", ""),
            os.environ.get("YUQUE_PRODUCT_DOCS_REPO", ""),
        ) if value.strip()
    }
    client = ReadonlyYuqueClient(token, repositories)
    host = os.environ.get("FASTMCP_HOST", "127.0.0.1")
    if host not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("只读知识服务仅允许绑定回环地址，请勿直接暴露公网")
    mcp = FastMCP("wecom-liab readonly knowledge", host=host,
                  port=int(os.environ.get("FASTMCP_PORT", "8003")), json_response=True)

    @mcp.tool()
    def yuque_get_doc(repo_id: str, doc_id: str) -> dict:
        """读取显式授权库中的文档，不执行写操作。"""
        return client.document(repo_id, doc_id)

    @mcp.tool()
    def yuque_get_sub_doc(repo_id: str, doc_id: str) -> dict:
        """列出授权文档内的授权子文档引用。"""
        return client.subdocuments(repo_id, doc_id)

    @mcp.tool()
    def yuque_search(query: str, type: str = "doc") -> dict:
        """只读搜索，并限制返回结果为配置的知识库。"""
        return client.search(query, type)

    return mcp


if __name__ == "__main__":
    create_mcp().run(transport="streamable-http")

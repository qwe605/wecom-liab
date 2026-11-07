"""Skill 五级分类路径的数据契约、规范化和目录树聚合。"""

from __future__ import annotations

import re
from typing import Any

from backend.schemas.yuque_taxonomy import normalize_yuque_category


FULL_LEVELS = (
    "knowledge_domain",
    "product_category",
    "product_subcategory",
    "product_model",
    "topic",
)
SHORT_LEVELS = ("knowledge_domain", "topic")
KNOWLEDGE_DOMAINS = {
    "product_knowledge": "产品知识",
    "troubleshooting": "排障步骤",
    "service_script": "客服话术",
    "service_process": "服务流程",
    "communication_method": "沟通方法",
    "review_policy": "系统审核规则",
}
BUSINESS_KNOWLEDGE_DOMAINS = {
    code: name for code, name in KNOWLEDGE_DOMAINS.items() if code != "review_policy"
}
INTENT_KNOWLEDGE_ROUTES = {
    "参数咨询": ("product_knowledge", ()),
    "产品选型": ("product_knowledge", ("communication_method",)),
    "报价交期": ("service_script", ("product_knowledge",)),
    "安装配置": ("troubleshooting", ("product_knowledge",)),
    "连接通信": ("troubleshooting", ("product_knowledge",)),
    "故障排查": ("troubleshooting", ("product_knowledge",)),
    "售后退换": ("service_process", ("service_script", "product_knowledge")),
    "投诉风险": ("service_process", ("service_script", "communication_method")),
    "订单进度": ("service_process", ("service_script",)),
    "其他": ("communication_method", ()),
}


def knowledge_route(intent: str) -> tuple[str, tuple[str, ...]]:
    """返回受控意图的主知识域和补充域，并阻止内部审核域进入回答。"""

    route = INTENT_KNOWLEDGE_ROUTES.get(intent)
    if route is None:
        raise ValueError("客户意图不在受控知识路由中")
    if any(code not in BUSINESS_KNOWLEDGE_DOMAINS for code in (route[0], *route[1])):
        raise ValueError("客服回答知识路由包含非业务知识域")
    return route
SHORT_PATH_DOMAINS = {
    "service_script",
    "service_process",
    "communication_method",
    "review_policy",
}
LEGACY_DOMAIN_CODES = {"reply_script": "service_script"}


def taxonomy_code(value: str) -> str:
    """把分类名称转换为稳定的默认编码。

    Args:
        value: AI 或审核人员填写的分类名称。

    Returns:
        小写下划线编码；中文名称无法转写时使用原文，保证信息不丢失。
    """

    text = value.strip()
    ascii_code = re.sub(r"[^a-z0-9]+", "_", text.casefold()).strip("_")
    return ascii_code or text


def normalize_taxonomy_path(
    raw_path: Any,
    *,
    require_publishable: bool = False,
) -> list[dict[str, str]]:
    """规范化并校验一条 Skill 分类路径。

    Args:
        raw_path: 按层级排列的字典数组，每项包含 ``level/name``，可选 ``code``。
        require_publishable: 为真时要求路径满足正式发布规则；候选阶段允许空路径。

    Returns:
        只包含 ``level/code/name`` 的安全有序路径。

    Raises:
        ValueError: 路径结构、层级顺序或知识域不符合项目设计书。
    """

    if raw_path in (None, ""):
        raw_path = []
    if not isinstance(raw_path, list):
        raise ValueError("Skill 分类路径必须是数组")
    normalized: list[dict[str, str]] = []
    for item in raw_path:
        if not isinstance(item, dict):
            raise ValueError("Skill 分类路径节点必须是对象")
        level = str(item.get("level") or "").strip()
        name = str(item.get("name") or "").strip()
        if not level or not name:
            raise ValueError("Skill 分类路径的层级和名称不能为空")
        code = str(item.get("code") or "").strip() or taxonomy_code(name)
        if level == "knowledge_domain":
            code = LEGACY_DOMAIN_CODES.get(code, code)
            if code not in KNOWLEDGE_DOMAINS:
                raise ValueError("Skill 知识域不在受控范围内")
            name = KNOWLEDGE_DOMAINS[code]
        elif level == "product_category":
            # 产品大类只能来自专业人员维护的语雀一级目录；保留旧编码兼容
            # 测试和历史 Skill，但新候选会被统一为目录原名。
            catalog_category = normalize_yuque_category(code) or normalize_yuque_category(name)
            if catalog_category is not None:
                code, name = catalog_category
        normalized.append({"level": level, "code": code, "name": name})

    levels = tuple(item["level"] for item in normalized)
    if not normalized and not require_publishable:
        return []
    if levels not in {FULL_LEVELS, SHORT_LEVELS}:
        raise ValueError(
            "Skill 分类路径必须为完整五级，或使用知识域到叶子主题的通用短路径"
        )
    if levels == SHORT_LEVELS and normalized[0]["code"] not in SHORT_PATH_DOMAINS:
        raise ValueError("该知识域必须填写产品大类、产品小类和具体型号")
    return normalized


def taxonomy_matches(
    path: list[dict[str, str]],
    filters: dict[str, str],
) -> bool:
    """判断分类路径是否命中接口提供的逐级筛选条件。

    Args:
        path: 已规范化的 Skill 分类路径。
        filters: 层级名到目标编码或名称的非空映射。

    Returns:
        所有筛选层级均匹配时返回 ``True``。
    """

    values = {
        item["level"]: {item["code"].casefold(), item["name"].casefold()}
        for item in path
    }
    return all(
        value.strip().casefold() in values.get(level, set())
        for level, value in filters.items()
        if value.strip()
    )


def build_taxonomy_tree(
    skills: list[dict[str, Any]], *, include_skills: bool = True
) -> list[dict[str, Any]]:
    """根据已发布 Skill 的路径实时聚合知识目录树。

    Args:
        skills: Repository 返回的已发布 Skill 公开字段。

    Returns:
        根节点数组；每个节点包含层级、编码、名称、数量、子节点和叶子页面。
    """

    roots: list[dict[str, Any]] = []
    for skill in skills:
        try:
            path = normalize_taxonomy_path(
                skill.get("taxonomy_path"), require_publishable=True
            )
        except ValueError:
            # 升级前发布的 Skill 没有分类路径时不伪造分类，也不能拖垮整棵目录树；
            # 它仍保留在已发布审核列表中，等待 reviewer/admin 人工补齐。
            continue
        children = roots
        for part in path:
            node = next(
                (
                    item
                    for item in children
                    if item["level"] == part["level"] and item["code"] == part["code"]
                ),
                None,
            )
            if node is None:
                node = {**part, "skill_count": 0, "children": [], "skills": []}
                children.append(node)
            node["skill_count"] += 1
            children = node["children"]
        if include_skills:
            node["skills"].append(
                {
                    "id": skill["id"],
                    "title": skill["title"],
                    "content": skill["content"],
                    "taxonomy_path": path,
                }
            )
    return roots

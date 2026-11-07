"""语雀专业资料目录的受控产品分类。

公开版本使用合成分类演示规范化和目录聚合机制。实际部署的分类必须由
资料所有者授权提供，不能直接复制内部知识库目录。
"""

from __future__ import annotations

from typing import Any

import re


def taxonomy_code(value: str) -> str:
    """将目录名称转换为稳定编码；中文名称保留原文避免信息丢失。"""

    text = str(value or "").strip()
    return re.sub(r"[^a-z0-9]+", "_", text.casefold()).strip("_") or text


# 一级目录是产品大类；二级目录是产品小类。演示名称不对应真实资料库，编码用于
# taxonomy_path 和检索过滤，保证中文目录在接口、全文和向量检索中都可复用。
# 公开版本只提供合成的分类示例，不包含企业内部产品目录。
YUQUE_PRODUCT_CATALOG: dict[str, dict[str, Any]] = {
    "attitude_sensor": {"name": "姿态传感器", "children": ["姿态传感器模组类"]},
    "iot_sensor": {"name": "物联传感器", "children": ["演示网关"]},
    "industry_application_sensor": {"name": "行业应用传感器", "children": ["演示模块"]},
}

YUQUE_PRODUCT_CATEGORY_ALIASES = {
    **{item["name"]: code for code, item in YUQUE_PRODUCT_CATALOG.items()},
    "姿态传感器模组类": "attitude_sensor",
    "物联网传感器": "iot_sensor",
}


def normalize_yuque_category(value: str) -> tuple[str, str] | None:
    """把语雀一级目录名称或编码规范化为 ``(code, name)``。"""

    text = str(value or "").strip()
    if not text:
        return None
    code = YUQUE_PRODUCT_CATEGORY_ALIASES.get(text, text.casefold())
    item = YUQUE_PRODUCT_CATALOG.get(code)
    if item is None:
        return None
    return code, str(item["name"])


def yuque_catalog_tree() -> list[dict[str, Any]]:
    """返回审核台可展示的专业资料目录骨架，不包含 Skill 正文。"""

    return [
        {
            "level": "product_category",
            "code": code,
            "name": item["name"],
            "children": [
                {"level": "product_subcategory", "code": taxonomy_code(name), "name": name}
                for name in item["children"]
            ],
        }
        for code, item in YUQUE_PRODUCT_CATALOG.items()
    ]

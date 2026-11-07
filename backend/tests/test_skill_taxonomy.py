"""Skill 五级分类规则和实时目录聚合测试。"""

import unittest

from backend.schemas.skill_taxonomy import (
    BUSINESS_KNOWLEDGE_DOMAINS,
    INTENT_KNOWLEDGE_ROUTES,
    build_taxonomy_tree,
    knowledge_route,
    normalize_taxonomy_path,
)
from backend.schemas.yuque_taxonomy import YUQUE_PRODUCT_CATALOG, normalize_yuque_category


def full_path(model: str = "DEMO102C产品资料") -> list[dict[str, str]]:
    """构造符合项目设计书的产品知识五级路径。"""

    return [
        {"level": "knowledge_domain", "code": "product_knowledge", "name": "产品知识"},
        {"level": "product_category", "code": "attitude_sensor", "name": "姿态传感器"},
        {"level": "product_subcategory", "code": "attitude_module", "name": "姿态传感器模组类"},
        {"level": "product_model", "code": model.casefold(), "name": model},
        {"level": "topic", "code": "specification", "name": "产品规格书"},
    ]


class SkillTaxonomyTest(unittest.TestCase):
    """验证路径最小结构、短路径例外和目录共享前缀。"""

    def test_full_product_path_is_normalized(self):
        """完整产品路径应保持固定五级顺序并规范知识域名称。"""

        path = normalize_taxonomy_path(full_path(), require_publishable=True)

        self.assertEqual(len(path), 5)
        self.assertEqual(path[0]["name"], "产品知识")
        self.assertEqual(path[-1]["level"], "topic")

    def test_all_intent_routes_only_use_customer_facing_domains(self):
        """所有受控意图路由都必须复用业务知识域，审核规则不得混入回答。"""

        self.assertEqual(len(INTENT_KNOWLEDGE_ROUTES), 10)
        for intent in INTENT_KNOWLEDGE_ROUTES:
            primary, supplements = knowledge_route(intent)
            self.assertIn(primary, BUSINESS_KNOWLEDGE_DOMAINS)
            self.assertTrue(all(item in BUSINESS_KNOWLEDGE_DOMAINS for item in supplements))
            self.assertNotIn("review_policy", (primary, *supplements))

    def test_generic_service_script_can_use_short_path(self):
        """无产品维度的客服话术允许知识域到主题的短路径。"""

        path = normalize_taxonomy_path(
            [
                {"level": "knowledge_domain", "code": "service_script", "name": "客服话术"},
                {"level": "topic", "code": "delay", "name": "等待话术"},
            ],
            require_publishable=True,
        )

        self.assertEqual([item["level"] for item in path], ["knowledge_domain", "topic"])

    def test_product_knowledge_short_path_is_rejected(self):
        """产品知识不能缺失产品大类、小类和具体型号。"""

        with self.assertRaisesRegex(ValueError, "必须填写产品"):
            normalize_taxonomy_path(
                [
                    {"level": "knowledge_domain", "code": "product_knowledge", "name": "产品知识"},
                    {"level": "topic", "code": "specification", "name": "产品规格书"},
                ],
                require_publishable=True,
            )

    def test_tree_aggregates_shared_path_without_extra_entities(self):
        """两篇页面应共享路径节点，目录数量由已发布页面实时汇总。"""

        skills = [
            {"id": 1, "title": "规格说明", "content": "正文一", "taxonomy_path": full_path()},
            {"id": 2, "title": "补充规格", "content": "正文二", "taxonomy_path": full_path()},
        ]

        tree = build_taxonomy_tree(skills)

        self.assertEqual(len(tree), 1)
        self.assertEqual(tree[0]["skill_count"], 2)
        topic = tree[0]["children"][0]["children"][0]["children"][0]["children"][0]
        self.assertEqual(len(topic["skills"]), 2)
        self.assertEqual(topic["skills"][0]["taxonomy_path"], full_path())

    def test_tree_skips_legacy_skill_without_taxonomy(self):
        """历史已发布 Skill 没有路径时不应伪造分类或导致整棵树失败。"""

        tree = build_taxonomy_tree(
            [
                {"id": 1, "title": "历史知识", "content": "待人工分类", "taxonomy_path": []},
                {"id": 2, "title": "规格说明", "content": "正文", "taxonomy_path": full_path()},
            ]
        )

        self.assertEqual(len(tree), 1)
        self.assertEqual(tree[0]["skill_count"], 1)

    def test_navigation_tree_omits_skill_bodies(self):
        """大规模目录接口只能返回分类和数量，正文应由分页接口按需读取。"""

        tree = build_taxonomy_tree(
            [{"id": 1, "title": "规格说明", "content": "很长的正文", "taxonomy_path": full_path()}],
            include_skills=False,
        )

        topic = tree[0]["children"][0]["children"][0]["children"][0]["children"][0]
        self.assertEqual(topic["skill_count"], 1)
        self.assertEqual(topic["skills"], [])

    def test_professional_yuque_catalog_normalizes_product_root(self):
        """专业目录一级分类应可由中文名称稳定映射为受控编码。"""

        self.assertEqual(normalize_yuque_category("姿态传感器"), ("attitude_sensor", "姿态传感器"))
        self.assertIn("industry_application_sensor", YUQUE_PRODUCT_CATALOG)


if __name__ == "__main__":
    unittest.main()

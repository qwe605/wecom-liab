"""已发布 Skill 按分类路径隔离召回的仓储测试。"""

import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database.base import Base
from backend.database.models import Skill
from backend.database.repositories.knowledge_repository import (
    PublishedSkillKnowledgeRepository,
)


def taxonomy(model: str) -> list[dict[str, str]]:
    """构造指定型号的标准排障路径。"""

    return [
        {"level": "knowledge_domain", "code": "troubleshooting", "name": "排障步骤"},
        {"level": "product_category", "code": "attitude_sensor", "name": "姿态传感器"},
        {"level": "product_subcategory", "code": "attitude_module", "name": "姿态传感器模组类"},
        {"level": "product_model", "code": model.casefold(), "name": model},
        {"level": "topic", "code": "connection", "name": "连接排查"},
    ]


class PublishedSkillKnowledgeRepositoryTest(unittest.TestCase):
    """验证关键词相同的不同型号 Skill 不会发生跨分支召回。"""

    def test_taxonomy_filter_keeps_locked_product_branch(self):
        """锁定 DEMO102C 后只应返回 DEMO102C 分支，不得返回 DEMO901A。"""

        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine)
        factory = sessionmaker(engine, expire_on_commit=False)
        with factory.begin() as session:
            session.add_all(
                [
                    Skill(id=1, title="DEMO102C连接排查", content="检查串口连接", taxonomy_path=taxonomy("DEMO102C"), status="published"),
                    Skill(id=2, title="DEMO901A连接排查", content="检查串口连接", taxonomy_path=taxonomy("DEMO901A"), status="published"),
                ]
            )

        result = PublishedSkillKnowledgeRepository(factory).search(
            None,
            "串口连接",
            taxonomy_filters={
                "knowledge_domain": "troubleshooting",
                "product_model": "DEMO102C",
            },
        )

        self.assertEqual([item.title for item in result], ["DEMO102C连接排查"])
        self.assertEqual(result[0].metadata["taxonomy_path"][3]["name"], "DEMO102C")

    def test_expired_skill_is_not_retrieved(self):
        """到期 Skill 保留数据，但不得进入客服回答检索。"""

        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine)
        factory = sessionmaker(engine, expire_on_commit=False)
        with factory.begin() as session:
            session.add(Skill(
                id=3, title="DEMO102C连接排查", content="检查串口连接",
                taxonomy_path=taxonomy("DEMO102C"), status="published",
                expires_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1),
            ))

        result = PublishedSkillKnowledgeRepository(factory).search(
            None, "串口连接",
            taxonomy_filters={"knowledge_domain": "troubleshooting", "product_model": "DEMO102C"},
        )

        self.assertEqual(result, [])

    def test_vector_result_is_rechecked_against_locked_model(self):
        """向量服务即使返回错误型号，也必须被主进程硬过滤丢弃。"""

        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine)
        factory = sessionmaker(engine, expire_on_commit=False)

        class WrongModelVectorClient:
            """模拟返回跨型号候选的异常向量服务。"""

            def search(self, query, filters, limit):
                del query, filters, limit
                return [{
                    "skill_id": 2, "title": "DEMO901A连接", "content": "检查连接",
                    "taxonomy_path": taxonomy("DEMO901A"), "score": 0.99,
                }], {"fallback_reason": "", "embedding_model": "small", "index_version": "v1", "vector_ms": 1}

        result = PublishedSkillKnowledgeRepository(
            factory, vector_client=WrongModelVectorClient(), strategy_version="hybrid-small-v1"
        ).search(None, "无法通讯", taxonomy_filters={
            "knowledge_domain": "troubleshooting", "product_model": "DEMO102C",
        })

        self.assertEqual(result, [])

    def test_vector_failure_keeps_lexical_results(self):
        """小模型不可用时必须无感保留关键词召回结果。"""

        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine)
        factory = sessionmaker(engine, expire_on_commit=False)
        with factory.begin() as session:
            session.add(Skill(id=4, title="DEMO102C连接排查", content="检查串口连接", taxonomy_path=taxonomy("DEMO102C"), status="published"))

        class FailedVectorClient:
            """模拟本地单进程模型超时。"""

            def search(self, query, filters, limit):
                del query, filters, limit
                return [], {"fallback_reason": "轻量向量服务不可用", "embedding_model": "", "index_version": "", "vector_ms": 0}

        result = PublishedSkillKnowledgeRepository(
            factory, vector_client=FailedVectorClient(), strategy_version="hybrid-small-v1"
        ).search(None, "串口连接", taxonomy_filters={
            "knowledge_domain": "troubleshooting", "product_model": "DEMO102C",
        })

        self.assertEqual([item.reference_id for item in result], ["skill:4"])
        self.assertIn("轻量向量服务不可用", result[0].metadata["retrieval"]["fallback_reason"])

    def test_trace_keeps_candidates_beyond_final_top_five(self):
        """最终只返回一条时，审计轨迹仍应保留其他召回候选。"""

        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine)
        factory = sessionmaker(engine, expire_on_commit=False)
        with factory.begin() as session:
            session.add_all([
                Skill(
                    id=10 + offset,
                    title=f"DEMO102C连接排查{offset}",
                    content="检查串口连接",
                    taxonomy_path=taxonomy("DEMO102C"),
                    status="published",
                )
                for offset in range(3)
            ])

        references, trace = PublishedSkillKnowledgeRepository(factory).search_with_trace(
            None,
            "串口连接",
            limit=1,
            taxonomy_filters={
                "knowledge_domain": "troubleshooting",
                "product_model": "DEMO102C",
            },
        )

        self.assertEqual(len(references), 1)
        self.assertEqual(len(trace), 3)
        self.assertEqual([item["final_rank"] for item in trace], [1, 2, 3])


if __name__ == "__main__":
    unittest.main()

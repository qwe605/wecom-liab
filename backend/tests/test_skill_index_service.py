"""轻量向量索引对账与健康诊断测试。"""

import unittest

from backend.services.skill_index_service import SkillIndexService


class FakeIndexRepository:
    """提供索引服务测试所需的最小数据库契约。"""

    def __init__(self, active_count: int = 1):
        """保存当前 MySQL 应索引数量。"""

        self._active_count = active_count

    def pending_job_ids(self):
        """模拟没有历史 outbox 的首次部署。"""

        return []

    def active_count(self):
        """返回当前有效 Skill 数量。"""

        return self._active_count

    def active_documents(self):
        """返回重建需要的最小 Skill 文档。"""

        return [{"skill_id": 1, "version": 1, "title": "标题", "content": "正文", "taxonomy_path": []}]

    def job_counts(self):
        """模拟一个已耗尽重试的失败任务。"""

        return {"failed": 1}


class FakeIndex:
    """记录是否因数量不一致触发全量重建。"""

    def __init__(self):
        """初始化为空且未就绪的索引。"""

        self.rebuild_calls = 0
        self._diagnostics = {
            "ready": False,
            "count": 0,
            "dimension": 0,
            "index_version": "",
            "embedding_model": "small",
        }

    @property
    def diagnostics(self):
        """返回当前模拟索引状态。"""

        return dict(self._diagnostics)

    def rebuild(self, documents):
        """模拟成功构建并返回新的诊断结果。"""

        self.rebuild_calls += 1
        self._diagnostics.update(ready=True, count=len(documents), index_version="v1")
        return dict(self._diagnostics)


class SkillIndexServiceTest(unittest.TestCase):
    """验证首次部署、自然到期对账和失败任务可观测性。"""

    def test_missing_index_is_reconciled_without_outbox(self):
        """历史没有 outbox 时也应按 MySQL 有效数量自动建立索引。"""

        repository = FakeIndexRepository(active_count=1)
        index = FakeIndex()
        service = SkillIndexService(repository, index)

        result = service.process_pending()

        self.assertTrue(result["reconciled"])
        self.assertEqual(index.rebuild_calls, 1)
        self.assertEqual(result["count"], 1)

    def test_health_reports_failed_outbox_jobs(self):
        """重试耗尽任务必须进入健康诊断，不能被后台循环静默吞掉。"""

        service = SkillIndexService(FakeIndexRepository(), FakeIndex())

        self.assertEqual(service.diagnostics["failed_jobs"], 1)


if __name__ == "__main__":
    unittest.main()

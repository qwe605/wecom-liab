"""企业微信会话内容存档适配器纯逻辑测试。"""

import json
import queue
import unittest
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from backend.integrations.wecom_archive import WeComArchiveClient, _message_text, normalize_message


class WeComArchiveNormalizationTest(unittest.TestCase):
    """验证官方明文被转换为全局路由协议。"""

    def test_text_message_keeps_sequence_room_and_participants(self):
        """规范消息必须保留全局序号、roomid、参与者和正文。"""

        raw = {
            "msgid": "message-1",
            "from": "wma_customer",
            "tolist": ["demo_staff_01"],
            "roomid": "wr_group_1",
            "msgtime": 1785900000000,
            "msgtype": "text",
            "text": {"content": "请帮我确认参数"},
        }
        result = normalize_message(raw, sequence=7184001)
        self.assertEqual(_message_text(raw), "请帮我确认参数")
        self.assertEqual(result["archive_seq"], 7184001)
        self.assertEqual(result["chat_id"], "wr_group_1")
        self.assertEqual(result["participant_ids"], ["demo_staff_01", "wma_customer"])
        self.assertEqual(result["sender_type"], "customer")

    def test_control_character_in_message_text_can_be_parsed(self):
        """历史正文含原始换行时不能阻断整页同步。"""

        raw = '{"msgtype":"text","text":{"content":"第一行\n第二行"}}'
        message = json.loads(raw, strict=False)
        self.assertEqual(_message_text(message), "第一行\n第二行")


class FakeWorkerStdout:
    """提供可由后台读取线程阻塞消费的模拟标准输出。"""

    def __init__(self):
        """初始化逐行响应队列。"""

        self.lines = queue.Queue()

    def __iter__(self):
        """返回当前阻塞迭代器。"""

        return self

    def __next__(self):
        """等待下一行；None 表示模拟进程已结束。"""

        line = self.lines.get(timeout=2)
        if line is None:
            raise StopIteration
        return line


class FakeWorkerStdin:
    """接收父进程请求并立即生成对应模拟响应。"""

    def __init__(self, process):
        """绑定需要接收响应和关闭信号的模拟进程。"""

        self.process = process

    def write(self, raw):
        """把 page 请求转换成两条乱序消息，close 请求结束进程。"""

        request = json.loads(raw)
        if request.get("action") == "close":
            self.process.returncode = 0
            self.process.stdout.lines.put(None)
            return len(raw)
        seq = int(request["seq"])
        payload = {
            "ok": True,
            "records": [
                {"seq": seq + 2, "from": "staff", "tolist": ["wm1"], "msgtype": "text", "text": {"content": "b"}},
                {"seq": seq + 1, "from": "wm1", "tolist": ["staff"], "msgtype": "text", "text": {"content": "a"}},
            ],
        }
        self.process.stdout.lines.put(json.dumps(payload, ensure_ascii=False) + "\n")
        return len(raw)

    def flush(self):
        """模拟文本管道刷新，无需额外操作。"""

    def close(self):
        """模拟父进程关闭输入管道。"""


class FakeWorkerProcess:
    """模拟支持逐行双向通信的长驻 SDK 子进程。"""

    def __init__(self):
        """创建输入输出管道和进程状态。"""

        self.returncode = None
        self.stdout = FakeWorkerStdout()
        self.stdin = FakeWorkerStdin(self)
        self.stderr = StringIO()

    def poll(self):
        """返回当前模拟退出码。"""

        return self.returncode

    def wait(self, timeout=None):
        """返回模拟退出码；测试中的 close 会先设置为零。"""

        del timeout
        return self.returncode

    def terminate(self):
        """模拟温和终止。"""

        self.returncode = -15
        self.stdout.lines.put(None)

    def kill(self):
        """模拟强制终止。"""

        self.returncode = -9
        self.stdout.lines.put(None)


class WeComArchiveProcessIsolationTest(unittest.TestCase):
    """验证业务分页复用同一个独立 SDK 子进程。"""

    @patch("backend.integrations.wecom_archive.subprocess.Popen")
    def test_fetch_pages_reuse_worker_and_normalize_each_page(self, popen):
        """连续分页应只启动一次子进程，并分别规范化和排序。"""

        popen.return_value = FakeWorkerProcess()
        settings = SimpleNamespace(
            wecom_archive_seed_seq=0,
            wecom_archive_page_size=1000,
            wecom_archive_timeout_seconds=30,
        )
        client = WeComArchiveClient(settings)
        first = client.fetch_page(100, 1000)
        second = client.fetch_page(102, 1000)
        client.close()

        self.assertEqual([item["archive_seq"] for item in first], [101, 102])
        self.assertEqual([item["archive_seq"] for item in second], [103, 104])
        self.assertEqual(popen.call_count, 1)


if __name__ == "__main__":
    unittest.main()

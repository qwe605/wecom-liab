"""企业微信会话内容存档的业务进程协议与装配入口。"""

from __future__ import annotations

from typing import Any, Protocol

from backend.config import Settings


class MessageArchive(Protocol):
    """消息同步服务依赖的最小官方归档协议。"""

    seed_cursor: int

    def fetch_page(self, start_seq: int, limit: int) -> list[dict[str, Any]]:
        """从官方全局序号后拉取、解密并规范化一页消息。

        Args:
            start_seq: 上一条已经完整提交的全局归档序号。
            limit: 本页最大消息数。

        Returns:
            按 ``archive_seq`` 升序排列的规范消息。
        """


def build_message_archive(settings: Settings) -> MessageArchive:
    """装配唯一的企业微信会话内容存档适配器。

    Args:
        settings: 应用配置快照。

    Returns:
        通过独立子进程调用官方 SDK 的适配器。
    """

    from backend.integrations.wecom_archive import WeComArchiveClient

    return WeComArchiveClient(settings)

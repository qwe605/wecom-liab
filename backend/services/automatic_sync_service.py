"""企业微信会话内容存档后台自动同步服务。"""

from __future__ import annotations

import logging
import time

from backend.config import Settings, get_settings
from backend.database.session import session_scope
from backend.integrations.message_archive import build_message_archive
from backend.services.message_sync_service import MessageSyncService


LOGGER = logging.getLogger("kefubao.automatic_sync")


class AutomaticSyncService:
    """持续把企业微信全局新增消息写入 MySQL。"""

    def __init__(self, settings: Settings):
        """保存自动同步频率、错误退避和 SDK 配置。"""

        self.settings = settings
        # 自动同步进程全生命周期复用同一个 Integration，使其内部独立 SDK
        # 子进程保持长驻，避免每个空轮询都重新加载动态库、私钥并执行 Init。
        self.archive = build_message_archive(settings)

    def run_once(self) -> dict[str, int | bool]:
        """在单个数据库事务中完整处理一页全局消息。"""

        with session_scope() as session:
            return MessageSyncService(
                session,
                self.settings,
                self.archive,
            ).sync_next_page()

    def run_forever(self) -> None:
        """持续追平消息流；到达末尾后按配置频率轮询。"""

        try:
            while True:
                try:
                    result = self.run_once()
                    if result["imported"] or not result["is_latest"]:
                        LOGGER.info(
                            "自动同步完成: scanned=%s imported=%s skipped=%s next_seq=%s is_latest=%s",
                            result.get("scanned", 0),
                            result.get("imported", 0),
                            result.get("skipped", 0),
                            result.get("next_seq", 0),
                            result.get("is_latest", False),
                        )
                    delay = (
                        self.settings.wecom_archive_auto_sync_seconds
                        if result["is_latest"]
                        else 0.0
                    )
                except Exception:
                    LOGGER.exception("自动同步失败，当前事务已回滚并等待下轮重试")
                    delay = self.settings.wecom_archive_auto_sync_error_seconds
                time.sleep(delay)
        finally:
            close = getattr(self.archive, "close", None)
            if close is not None:
                close()


def main() -> None:
    """配置日志并启动企业微信全局自动同步循环。"""

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    AutomaticSyncService(get_settings()).run_forever()


if __name__ == "__main__":
    main()

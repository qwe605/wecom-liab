#!/usr/bin/env python3
"""每日同步企业微信员工与外部联系人的显示姓名。

会话内容存档只保证返回发送者官方 ID，不保证附带可展示姓名。本任务使用企业
微信通讯录与客户联系官方接口补齐现有 ``users`` 和 ``customers`` 记录。它不
参与消息拉取，也不改变登录权限、角色或会话关系。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from backend.config import Settings, get_settings
from backend.database.repositories import ConversationRepository, UserRepository
from backend.database.session import session_scope
from backend.integrations.wecom import WeComClient


class ProfileSyncService:
    """编排企业微信资料读取和 MySQL 姓名更新。"""

    def __init__(
        self,
        session: Session,
        settings: Settings,
        wecom: WeComClient | None = None,
    ):
        """注入数据库事务、运行配置和可替换的企业微信适配器。

        Args:
            session: SQLAlchemy 事务会话。
            settings: 当前运行配置。
            wecom: 测试或正式企业微信客户端；为空时自动创建。
        """

        self.session = session
        self.users = UserRepository(session)
        self.conversations = ConversationRepository(session)
        self.wecom = wecom or WeComClient(settings)

    def sync_all_visible_profiles(self) -> dict[str, Any]:
        """同步当前应用权限范围内的全部员工和客户姓名。

        Returns:
            包含可见数量和实际更新数量的执行统计。

        Raises:
            RuntimeError: 企业微信接口发生不可忽略的业务错误。
        """

        candidate_userids = self.users.list_wecom_userids()
        candidate_customer_ids = self.conversations.unresolved_customer_ids()
        candidate_group_ids = self.conversations.group_chat_ids()
        employees = self.wecom.visible_employee_profiles(candidate_userids)
        contacts = self.wecom.external_contact_profiles(
            [item["wecom_userid"] for item in employees],
            candidate_customer_ids,
        )
        groups = self.wecom.external_group_profiles(candidate_group_ids)

        # 客户和群资料接口可能需要十余分钟才能完成分页。期间消息自动同步会
        # 持续新增员工或客户；结束外部调用后先关闭原只读事务，确保后续查询
        # 能看到实时同步已经提交的数据，避免按旧快照误判“不存在”并重复插入。
        self.session.rollback()

        # 群详情的 need_name=1 能补齐客户联系批量接口遗漏的群成员。以官方 ID
        # 合并后再统一更新，避免同一人员在多个群中产生重复数据库写入。
        employee_names = {
            item["wecom_userid"]: item["name"] for item in employees
        }
        customer_names = {
            item["external_userid"]: item["name"] for item in contacts
        }
        for group in groups:
            for member in group["members"]:
                if member["type"] == "staff":
                    employee_names[member["id"]] = member["name"]
                else:
                    customer_names[member["id"]] = member["name"]
        employee_updated = sum(
            self.users.sync_display_name(userid, name)
            for userid, name in employee_names.items()
        )
        customer_updated = sum(
            self.conversations.sync_customer_name(
                external_userid,
                name,
            )
            for external_userid, name in customer_names.items()
        )
        group_updated = sum(
            self.conversations.sync_group_name(item["chat_id"], item["name"])
            for item in groups
        )
        return {
            "employee_candidates": len(candidate_userids),
            "employees_visible": len(employees),
            "employees_updated": employee_updated,
            "customers_visible": len(contacts),
            "customers_updated": customer_updated,
            "customer_candidates": len(candidate_customer_ids),
            "group_candidates": len(candidate_group_ids),
            "groups_visible": len(groups),
            "groups_updated": group_updated,
        }


def sync_profiles() -> dict[str, Any]:
    """在独立事务中执行一次资料同步并返回统计。"""

    settings = get_settings()
    with session_scope() as session:
        return ProfileSyncService(session, settings).sync_all_visible_profiles()


def main() -> int:
    """执行一次同步，供 systemd oneshot 服务调用。"""

    print(sync_profiles())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

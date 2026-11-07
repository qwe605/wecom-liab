#!/usr/bin/env python3
"""创建或更新客服宝审核管理账号。"""

from __future__ import annotations

import argparse
from getpass import getpass

from backend.database.repositories import UserRepository
from backend.database.session import session_scope
from backend.services.auth_service import hash_password


def main() -> int:
    """根据命令行参数幂等保存管理员账号。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", required=True)
    parser.add_argument("--wecom-userid", required=True)
    parser.add_argument("--name", default="系统管理员")
    args = parser.parse_args()
    # 密码只通过隐藏交互输入，避免出现在进程命令行、shell 历史或操作截图中。
    password = getpass("管理员密码（至少 8 位）: ")
    if password != getpass("确认密码: "):
        parser.error("两次输入的密码不一致")
    password_hash = hash_password(password)
    with session_scope() as session:
        UserRepository(session).save_admin(
            wecom_userid=args.wecom_userid,
            username=args.username,
            password_hash=password_hash,
            name=args.name,
        )
    print(f"管理员已保存: {args.username}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

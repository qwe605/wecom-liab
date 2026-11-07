"""企业微信 OAuth、JS-SDK 签名和用户身份适配。"""

from __future__ import annotations

import hashlib
import logging
import secrets
import time
from typing import Any
from urllib.parse import urlencode

from backend.config import Settings
from backend.integrations.http import get_json, post_json


LOGGER = logging.getLogger("kefubao.wecom")


class WeComClient:
    """封装企业微信服务端 API，并缓存短期凭据。"""

    def __init__(self, settings: Settings):
        """保存企业微信配置并初始化进程内短期 Token 缓存。"""
        self.settings = settings
        self._cache: dict[str, tuple[str, float]] = {}

    def access_token(self) -> str:
        """获取企业微信 access_token。"""

        return self._access_token(
            self.settings.wecom_corp_secret,
            "access_token:application",
        )

    def contacts_access_token(self) -> str:
        """获取通讯录接口使用的 access_token。

        Returns:
            使用通讯录 Secret 获取的 Token；未单独配置时回退自建应用 Secret。
        """

        secret = self.settings.wecom_contacts_secret or self.settings.wecom_corp_secret
        return self._access_token(secret, "access_token:contacts")

    def customer_contact_access_token(self) -> str:
        """获取客户联系接口使用的 access_token。

        Returns:
            使用客户联系 Secret 获取的 Token；未单独配置时回退自建应用 Secret。
        """

        secret = (
            self.settings.wecom_customer_contact_secret
            or self.settings.wecom_corp_secret
        )
        return self._access_token(secret, "access_token:customer-contact")

    def _access_token(self, secret: str, cache_key: str) -> str:
        """按指定 Secret 获取并缓存用途隔离的 access_token。

        Args:
            secret: 自建应用、通讯录或客户联系 Secret。
            cache_key: 区分不同权限 Token 的缓存键。

        Returns:
            企业微信 access_token。

        Raises:
            RuntimeError: 企业 ID 或当前用途 Secret 未配置完整。
        """

        cached = self._cache.get(cache_key)
        if cached and cached[1] > time.time() + 300:
            return cached[0]
        if not self.settings.wecom_corp_id or not secret:
            raise RuntimeError("企业微信企业 ID 或接口 Secret 未配置")
        query = urlencode(
            {
                "corpid": self.settings.wecom_corp_id,
                "corpsecret": secret,
            }
        )
        result = get_json(f"https://qyapi.weixin.qq.com/cgi-bin/gettoken?{query}")
        self._raise_on_error(result, "gettoken")
        token = str(result["access_token"])
        self._cache[cache_key] = (
            token,
            time.time() + int(result.get("expires_in") or 7200),
        )
        return token

    def oauth_user(self, code: str) -> dict[str, Any]:
        """把 H5 OAuth code 换成企业微信员工身份。"""

        if not code:
            raise ValueError("缺少 OAuth code")
        query = urlencode({"access_token": self.access_token(), "code": code})
        result = get_json(
            f"https://qyapi.weixin.qq.com/cgi-bin/user/getuserinfo?{query}"
        )
        self._raise_on_error(result, "h5 getuserinfo")
        userid = str(result.get("UserId") or result.get("userid") or "")
        if not userid:
            raise PermissionError("企业微信没有返回内部员工 UserId")
        return {"wecom_userid": userid, "raw": result}

    def visible_employee_profiles(
        self,
        candidate_userids: list[str] | None = None,
    ) -> list[dict[str, str]]:
        """读取当前应用可见范围内的员工姓名。

        优先按部门批量读取。应用只配置了个别成员而没有可见部门时，再使用数据库
        已发现的员工 ID 逐个补查。无权限或已经离开当前企业的候选 ID 会被跳过，
        避免一名无效成员阻断整次每日同步。

        Args:
            candidate_userids: 归档消息已发现、需要尝试补全姓名的员工 ID。

        Returns:
            去重后的 ``wecom_userid`` 与 ``name`` 列表。

        Raises:
            RuntimeError: 部门或成员批量接口返回非权限类业务错误。
        """

        token = self.contacts_access_token()
        query = urlencode({"access_token": token})
        department_result = get_json(
            f"https://qyapi.weixin.qq.com/cgi-bin/department/simplelist?{query}"
        )
        self._raise_on_error(department_result, "list departments")
        profiles: dict[str, str] = {}
        for department in department_result.get("department_id") or []:
            department_id = int(department.get("id") or 0)
            if department_id <= 0:
                continue
            member_query = urlencode(
                {
                    "access_token": token,
                    "department_id": department_id,
                    "fetch_child": 0,
                }
            )
            result = get_json(
                f"https://qyapi.weixin.qq.com/cgi-bin/user/list?{member_query}"
            )
            self._raise_on_error(result, f"list department {department_id} users")
            self._collect_employee_profiles(profiles, result.get("userlist") or [])

        # 归档消息可能包含不在应用部门可见范围中的员工。对这些候选逐个查询，
        # 既兼容“只给应用配置个别成员”的后台设置，也不会授予系统登录权限。
        for userid in dict.fromkeys(candidate_userids or []):
            normalized = str(userid or "").strip()
            if not normalized or normalized in profiles:
                continue
            user_query = urlencode({"access_token": token, "userid": normalized})
            result = get_json(
                f"https://qyapi.weixin.qq.com/cgi-bin/user/get?{user_query}"
            )
            error_code = int(result.get("errcode") or 0)
            if error_code in {40003, 60011, 60111}:
                continue
            self._raise_on_error(result, f"get user {normalized}")
            self._collect_employee_profiles(profiles, [result])
        return [
            {"wecom_userid": userid, "name": name}
            for userid, name in profiles.items()
        ]

    def external_contact_profiles(
        self,
        employee_userids: list[str],
        candidate_external_userids: list[str] | None = None,
    ) -> list[dict[str, str]]:
        """按可见员工批量读取其正在跟进的外部联系人姓名。

        Args:
            employee_userids: 已通过通讯录接口确认有效且可见的员工 ID。
            candidate_external_userids: 归档消息已经发现、仍需补齐姓名的客户 ID。

        Returns:
            去重后的 ``external_userid`` 与 ``name`` 列表。

        Raises:
            RuntimeError: 客户联系接口返回业务错误。
        """

        userids = list(dict.fromkeys(item for item in employee_userids if item))
        if not userids:
            return []
        token_query = urlencode(
            {"access_token": self.customer_contact_access_token()}
        )
        url = (
            "https://qyapi.weixin.qq.com/cgi-bin/"
            f"externalcontact/batch/get_by_user?{token_query}"
        )
        profiles: dict[str, str] = {}
        for offset in range(0, len(userids), 100):
            batch = userids[offset : offset + 100]
            cursor = ""
            while True:
                payload: dict[str, Any] = {"userid_list": batch, "limit": 100}
                if cursor:
                    payload["cursor"] = cursor
                result = post_json(url, payload)
                self._raise_on_error(result, "batch get external contacts")
                for item in result.get("external_contact_list") or []:
                    contact = item.get("external_contact") or {}
                    external_userid = str(contact.get("external_userid") or "").strip()
                    name = str(contact.get("name") or "").strip()
                    if external_userid and name:
                        profiles[external_userid] = name
                cursor = str(result.get("next_cursor") or "").strip()
                if not cursor:
                    break

        # 批量接口只能返回指定员工正在跟进的客户。配置了独立客户联系 Secret
        # 后，再利用归档已有 external_userid 调用官方客户详情接口补齐遗漏项。
        # 未显式配置时不进行大量逐条请求，避免用权限不足的应用 Token 空转。
        if self.settings.wecom_customer_contact_secret:
            for external_userid in dict.fromkeys(candidate_external_userids or []):
                normalized = str(external_userid or "").strip()
                if not normalized or normalized in profiles:
                    continue
                query = urlencode(
                    {
                        "access_token": self.customer_contact_access_token(),
                        "external_userid": normalized,
                    }
                )
                result = get_json(
                    "https://qyapi.weixin.qq.com/cgi-bin/externalcontact/get?"
                    f"{query}"
                )
                error_code = int(result.get("errcode") or 0)
                # 非当前企业客户、已流失客户和无可见权限的记录无法补齐，但不应
                # 阻断其他合法客户的每日资料同步。
                if error_code in {40096, 60011, 84061, 84074}:
                    continue
                self._raise_on_error(result, f"get external contact {normalized}")
                contact = result.get("external_contact") or {}
                name = str(contact.get("name") or "").strip()
                if name:
                    profiles[normalized] = name
        return [
            {"external_userid": external_userid, "name": name}
            for external_userid, name in profiles.items()
        ]

    def external_group_profiles(self, chat_ids: list[str]) -> list[dict[str, Any]]:
        """读取外部客户群名称及群成员显示名。

        企业微信会话内容存档只负责提供 ``roomid`` 和消息；群名称及成员名称通过
        客户联系“获取客户群详情”接口补齐。请求中的 ``need_name=1`` 使接口返回
        群成员名称，可同时补充员工和客户的展示信息。

        Args:
            chat_ids: 数据库中由归档消息发现的外部群聊 ID。

        Returns:
            包含 ``chat_id``、``name`` 和规范化 ``members`` 的群资料列表。

        Raises:
            RuntimeError: 接口返回非权限类、非群状态类业务错误。
        """

        # 不以“是否单独配置客户联系 Secret”判断接口权限。当前自建应用 Token
        # 可能已经拥有客户联系可见范围，此处统一复用 Token 回退逻辑并以企业微信
        # 的真实接口响应为准，避免本地配置误判导致所有群名都无法补齐。
        token_query = urlencode(
            {"access_token": self.customer_contact_access_token()}
        )
        url = (
            "https://qyapi.weixin.qq.com/cgi-bin/"
            f"externalcontact/groupchat/get?{token_query}"
        )
        profiles: list[dict[str, Any]] = []
        for chat_id in dict.fromkeys(chat_ids):
            normalized = str(chat_id or "").strip()
            if not normalized:
                continue
            result = post_json(url, {"chat_id": normalized, "need_name": 1})
            error_code = int(result.get("errcode") or 0)
            # 归档消息可能包含已解散群、内部群或当前企业无权读取的跨企业群。
            # 这些错误只影响当前 roomid，不能让其后的合法外部群停止同步。
            if error_code in {60011, 86001, 86003, 90207, 90501, 92002}:
                LOGGER.warning(
                    "无法读取外部群详情 errcode=%s",
                    error_code,
                )
                continue
            self._raise_on_error(result, f"get external group {normalized}")
            group = result.get("group_chat") or {}
            members: list[dict[str, str]] = []
            for item in group.get("member_list") or []:
                userid = str(item.get("userid") or "").strip()
                name = str(item.get("name") or item.get("group_nickname") or "").strip()
                member_type = "staff" if int(item.get("type") or 0) == 1 else "customer"
                if userid and name:
                    members.append({"id": userid, "name": name, "type": member_type})
            profiles.append(
                {
                    "chat_id": normalized,
                    "name": str(group.get("name") or "").strip(),
                    "members": members,
                }
            )
        return profiles

    def js_signature(self, page_url: str) -> dict[str, Any]:
        """生成 `wx.config` 和 `wx.agentConfig` 所需签名。"""

        if not page_url:
            raise ValueError("缺少签名页面 URL")
        timestamp = int(time.time())
        nonce = secrets.token_urlsafe(12)
        corp_ticket = self._ticket("jsapi")
        agent_ticket = self._ticket("agent_config")
        return {
            "corp_id": self.settings.wecom_corp_id,
            "agent_id": self.settings.wecom_agent_id,
            "timestamp": timestamp,
            "nonce_str": nonce,
            "url": page_url,
            "corp_signature": self._sign(corp_ticket, nonce, timestamp, page_url),
            "agent_signature": self._sign(agent_ticket, nonce, timestamp, page_url),
        }

    def _ticket(self, ticket_type: str) -> str:
        """获取并缓存 JS-SDK ticket。"""

        cache_key = f"ticket:{ticket_type}"
        cached = self._cache.get(cache_key)
        if cached and cached[1] > time.time() + 300:
            return cached[0]
        token = self.access_token()
        if ticket_type == "jsapi":
            query = urlencode({"access_token": token})
            url = f"https://qyapi.weixin.qq.com/cgi-bin/get_jsapi_ticket?{query}"
        else:
            query = urlencode({"access_token": token, "type": "agent_config"})
            url = f"https://qyapi.weixin.qq.com/cgi-bin/ticket/get?{query}"
        result = get_json(url)
        self._raise_on_error(result, f"get {ticket_type} ticket")
        ticket = str(result["ticket"])
        self._cache[cache_key] = (
            ticket,
            time.time() + int(result.get("expires_in") or 7200),
        )
        return ticket

    def _require_credentials(self) -> None:
        """验证企业微信服务端凭据已配置。"""

        if not all(
            [
                self.settings.wecom_corp_id,
                self.settings.wecom_agent_id,
                self.settings.wecom_corp_secret,
            ]
        ):
            raise RuntimeError("企业微信凭据未完整配置")

    @staticmethod
    def _collect_employee_profiles(
        target: dict[str, str],
        records: list[dict[str, Any]],
    ) -> None:
        """把企业微信成员响应合并到去重字典。

        Args:
            target: 以员工 ID 为键的目标字典。
            records: 企业微信成员详情数组。
        """

        for record in records:
            userid = str(record.get("userid") or record.get("UserId") or "").strip()
            name = str(record.get("name") or "").strip()
            if userid and name:
                target[userid] = name

    @staticmethod
    def _sign(ticket: str, nonce: str, timestamp: int, page_url: str) -> str:
        """按企业微信规则计算 SHA1 签名。"""

        raw = (
            f"jsapi_ticket={ticket}&noncestr={nonce}&timestamp={timestamp}"
            f"&url={page_url}"
        )
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _raise_on_error(result: dict[str, Any], operation: str) -> None:
        """只返回平台错误码，不透传原始响应、成员标识或上游回显凭据。"""

        if int(result.get("errcode") or 0) != 0:
            raise RuntimeError(f"企业微信接口请求失败 (errcode={int(result['errcode'])})")

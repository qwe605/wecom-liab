"""Skill 候选、审核和发布的 ORM 仓储。"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Text, cast, func, or_, select

from backend.database.models import (
    AIReply,
    Conversation,
    Customer,
    Message,
    Skill,
    SkillIndexJob,
    SkillReviewEvent,
    ReviewPolicySource,
    User,
)
from backend.database.session import get_session_factory
from backend.errors import ConcurrencyConflictError


class SkillRepository:
    """只负责 Skill 相关数据读写，不判断业务权限和审核决策。"""

    def __init__(self, session_factory=None):
        """初始化 Skill 仓储，并允许测试注入独立 Session 工厂。"""
        self.session_factory = session_factory or get_session_factory()

    @staticmethod
    def _now() -> datetime:
        """返回 MySQL DATETIME 使用的无时区 UTC 当前时间。"""

        return datetime.now(timezone.utc).replace(tzinfo=None)

    @staticmethod
    def _review_policy_condition():
        """返回兼容 MySQL 与 SQLite 的审核规则知识域筛选表达式。"""

        return cast(Skill.taxonomy_path, Text).like("%review_policy%")

    def get_user(self, wecom_userid: str) -> dict | None:
        """按企业微信员工 ID 返回审核所需的最小用户信息。"""
        with self.session_factory() as session:
            user = session.get(User, wecom_userid)
            if user is None:
                return None
            return {"wecom_userid": user.wecom_userid, "name": user.name, "role": user.role, "is_enabled": user.is_enabled}

    def get_reply(self, suggestion_id: int, conversation_id: int, operator_userid: str) -> dict | None:
        """读取属于指定客服和会话的 AI 建议，阻止跨会话提交候选。"""
        with self.session_factory() as session:
            row = session.scalar(select(AIReply).where(
                AIReply.id == suggestion_id,
                AIReply.conversation_id == conversation_id,
                AIReply.operator_userid == operator_userid,
            ))
            if row is None:
                return None
            return {
                "question_summary": row.question_summary,
                "context_message_ids": list(row.context_message_ids or []),
            }

    def save_candidate(self, suggestion_id: int, conversation_id: int, source_message_ids: list[int], title: str, content: str, score: float, submitted_by: str, taxonomy_path: list[dict[str, str]]) -> dict:
        """新增或更新尚未审核的 Skill 候选，并返回公开字段。"""
        with self.session_factory.begin() as session:
            row = session.scalar(select(Skill).where(Skill.ai_reply_id == suggestion_id).limit(1))
            if row is None:
                row = Skill(
                    source_conversation_id=conversation_id, ai_reply_id=suggestion_id,
                    source_message_ids=source_message_ids,
                    title=title[:255], content=content, quality_score=score,
                    taxonomy_path=taxonomy_path, status="pending", submitted_by=submitted_by,
                )
                session.add(row)
            elif row.status == "pending":
                row.title, row.content, row.quality_score = title[:255], content, score
                row.source_message_ids = source_message_ids
                row.taxonomy_path = taxonomy_path
            session.flush()
            return self._public(row, {})

    def list(self, status: str, limit: int) -> list[dict]:
        """按状态和质量分读取审核队列，Repository 不判断审核权限。"""
        with self.session_factory() as session:
            statement = select(Skill).where(~self._review_policy_condition())
            statement = statement.where(Skill.status == status) if status else statement.where(Skill.status != "rejected")
            rows = list(session.scalars(statement.order_by(Skill.quality_score.desc(), Skill.id.desc()).limit(limit)))
            users = {row.wecom_userid: row.name for row in session.scalars(select(User))}
            return [self._public(row, users) for row in rows]

    def list_review_pages(
        self,
        status: str,
        taxonomy_filters: dict[str, str],
        keyword: str,
        page: int,
        page_size: int,
    ) -> tuple[list[dict], int]:
        """按审核状态、分类和关键词在数据库内筛选并分页。"""

        with self.session_factory() as session:
            # 普通审核队列与系统审核规则分开运营，避免用户再次误以为
            # review_policy 是客服回答知识或普通候选。
            conditions = [~self._review_policy_condition()]
            if status:
                conditions.append(Skill.status == status)
            else:
                conditions.append(Skill.status != "archived")
            for level, value in taxonomy_filters.items():
                normalized = value.strip()
                if normalized:
                    conditions.append(
                        or_(
                            func.json_contains(
                                Skill.taxonomy_path,
                                func.json_object("level", level, "code", normalized),
                            ),
                            func.json_contains(
                                Skill.taxonomy_path,
                                func.json_object("level", level, "name", normalized),
                            ),
                        )
                    )
            if keyword.strip():
                pattern = f"%{keyword.strip()}%"
                conditions.append(
                    or_(Skill.title.like(pattern), Skill.content.like(pattern))
                )
            total = int(
                session.scalar(select(func.count(Skill.id)).where(*conditions)) or 0
            )
            rows = list(
                session.scalars(
                    select(Skill)
                    .where(*conditions)
                    .order_by(Skill.quality_score.desc(), Skill.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            )
            return [self._public(row, {}) for row in rows], total

    def review_detail(self, skill_id: int) -> dict:
        """按需读取审核工作区当前选中的完整候选。"""

        with self.session_factory() as session:
            row = session.get(Skill, skill_id)
            if row is None:
                raise LookupError("Skill 候选不存在")
            return self._public(row, {})

    def review_events(self, skill_id: int) -> list[dict]:
        """按时间正序返回一条 Skill 的完整不可变审核历史。"""

        with self.session_factory() as session:
            if session.get(Skill, skill_id) is None:
                raise LookupError("Skill 候选不存在")
            rows = list(
                session.scalars(
                    select(SkillReviewEvent)
                    .where(SkillReviewEvent.skill_id == skill_id)
                    .order_by(SkillReviewEvent.created_at.asc(), SkillReviewEvent.id.asc())
                )
            )
            return [
                {
                    "id": row.id,
                    "skill_id": row.skill_id,
                    "reviewer_userid": row.reviewer_userid or "",
                    "action": row.action,
                    "reason_codes": list(row.reason_codes or []),
                    "opinion": row.opinion,
                    "before_snapshot": dict(row.before_snapshot or {}),
                    "after_snapshot": dict(row.after_snapshot or {}),
                    "governance_snapshot": dict(row.governance_snapshot or {}),
                    "request_id": row.request_id,
                    "created_at": row.created_at.isoformat(),
                }
                for row in rows
            ]

    def published_in_path(
        self, taxonomy_path: list[dict[str, str]], exclude_skill_id: int
    ) -> list[dict]:
        """返回同一完整分类路径下仍有效的已发布 Skill，供发布治理比较。"""

        with self.session_factory() as session:
            rows = list(session.scalars(select(Skill).where(
                Skill.status == "published",
                Skill.id != exclude_skill_id,
                or_(Skill.expires_at.is_(None), Skill.expires_at > self._now()),
            )))
            return [
                self._public(row, {}) for row in rows
                if list(row.taxonomy_path or []) == taxonomy_path
            ]

    def list_published(self) -> list[dict]:
        """读取全部已发布 Skill，供 Service 实时聚合完整知识目录。"""

        with self.session_factory() as session:
            rows = list(
                session.scalars(
                    select(Skill)
                    .where(
                        Skill.status == "published",
                        ~self._review_policy_condition(),
                        or_(Skill.expires_at.is_(None), Skill.expires_at > self._now()),
                    )
                    .order_by(Skill.id.asc())
                )
            )
            return [self._public(row, {}) for row in rows]

    def list_published_pages(
        self,
        taxonomy_filters: dict[str, str],
        keyword: str,
        page: int,
        page_size: int,
    ) -> tuple[list[dict], int]:
        """在数据库内按分类和关键词筛选已发布页面并稳定分页。"""

        with self.session_factory() as session:
            conditions = [
                Skill.status == "published",
                ~self._review_policy_condition(),
                or_(Skill.expires_at.is_(None), Skill.expires_at > self._now()),
            ]
            for level, value in taxonomy_filters.items():
                normalized = value.strip()
                if not normalized:
                    continue
                # taxonomy_path 是有序 JSON 数组；同时匹配层级和值，防止同名分类串层。
                conditions.append(
                    or_(
                        func.json_contains(
                            Skill.taxonomy_path,
                            func.json_object("level", level, "code", normalized),
                        ),
                        func.json_contains(
                            Skill.taxonomy_path,
                            func.json_object("level", level, "name", normalized),
                        ),
                    )
                )
            if keyword.strip():
                pattern = f"%{keyword.strip()}%"
                conditions.append(
                    or_(Skill.title.like(pattern), Skill.content.like(pattern))
                )
            total = int(
                session.scalar(select(func.count(Skill.id)).where(*conditions)) or 0
            )
            rows = list(
                session.scalars(
                    select(Skill)
                    .where(*conditions)
                    .order_by(Skill.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            )
            return [self._public(row, {}) for row in rows], total

    def list_review_policy_rules(
        self,
        status: str,
        keyword: str,
        page: int,
        page_size: int,
    ) -> tuple[list[dict], int]:
        """分页返回系统审核规则 Skill，不与业务知识列表混排。"""

        with self.session_factory() as session:
            conditions = [self._review_policy_condition()]
            if status:
                conditions.append(Skill.status == status)
            if keyword.strip():
                pattern = f"%{keyword.strip()}%"
                conditions.append(
                    or_(Skill.title.like(pattern), Skill.content.like(pattern))
                )
            total = int(
                session.scalar(select(func.count(Skill.id)).where(*conditions)) or 0
            )
            rows = list(
                session.scalars(
                    select(Skill)
                    .where(*conditions)
                    .order_by(Skill.id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            )
            return [self._public(row, {}) for row in rows], total

    def review_policy_detail(self, policy_skill_id: int) -> dict:
        """读取审核规则正文及其原始通过/驳回事件证据。"""

        with self.session_factory() as session:
            row = session.get(Skill, policy_skill_id)
            if row is None or not self._is_review_policy(row.taxonomy_path):
                raise LookupError("审核规则 Skill 不存在")
            source_rows = list(
                session.execute(
                    select(SkillReviewEvent, Skill)
                    .join(
                        ReviewPolicySource,
                        ReviewPolicySource.review_event_id == SkillReviewEvent.id,
                    )
                    .join(Skill, Skill.id == SkillReviewEvent.skill_id)
                    .where(ReviewPolicySource.policy_skill_id == policy_skill_id)
                    .order_by(
                        SkillReviewEvent.created_at.asc(),
                        SkillReviewEvent.id.asc(),
                    )
                )
            )
            result = self._public(row, {})
            result["source_review_events"] = [
                {
                    "id": event.id,
                    "skill_id": event.skill_id,
                    "skill_title": reviewed_skill.title,
                    "action": event.action,
                    "reason_codes": list(event.reason_codes or []),
                    "opinion": event.opinion,
                    "before_snapshot": dict(event.before_snapshot or {}),
                    "after_snapshot": dict(event.after_snapshot or {}),
                    "reviewer_userid": event.reviewer_userid,
                    "created_at": event.created_at.isoformat(),
                }
                for event, reviewed_skill in source_rows
            ]
            return result

    def unlinked_review_decisions(self, limit: int = 500) -> list[dict]:
        """返回尚未生成审核规则草案的业务发布/驳回事件。"""

        with self.session_factory() as session:
            rows = list(
                session.execute(
                    select(SkillReviewEvent, Skill)
                    .join(Skill, Skill.id == SkillReviewEvent.skill_id)
                    .outerjoin(
                        ReviewPolicySource,
                        ReviewPolicySource.review_event_id == SkillReviewEvent.id,
                    )
                    .where(
                        SkillReviewEvent.action.in_(("publish", "reject")),
                        ReviewPolicySource.review_event_id.is_(None),
                        ~self._review_policy_condition(),
                    )
                    .order_by(SkillReviewEvent.id.asc())
                    .limit(max(1, min(int(limit), 1000)))
                )
            )
            return [
                {
                    "event_id": event.id,
                    "skill_id": skill.id,
                    "title": skill.title,
                    "taxonomy_path": list(skill.taxonomy_path or []),
                    "action": event.action,
                    "reason_codes": list(event.reason_codes or []),
                    "opinion": event.opinion,
                    "reviewer_userid": event.reviewer_userid,
                    "before_snapshot": dict(event.before_snapshot or {}),
                    "after_snapshot": dict(event.after_snapshot or {}),
                }
                for event, skill in rows
            ]

    def create_review_policy_drafts(
        self,
        drafts: list[dict],
        submitted_by: str,
    ) -> list[int]:
        """幂等补建历史审核规则草案，并返回新建 Skill ID。"""

        created: list[int] = []
        with self.session_factory.begin() as session:
            for draft in drafts:
                event_id = int(draft["review_event_id"])
                # 锁定来源事件后再检查关联，两个审核员同时打开规则页时，
                # 后进入的事务会看到前一事务已提交的关联而不会重复建草案。
                event = session.scalar(
                    select(SkillReviewEvent)
                    .where(SkillReviewEvent.id == event_id)
                    .with_for_update()
                )
                if event is None:
                    continue
                if session.scalar(
                    select(ReviewPolicySource).where(
                        ReviewPolicySource.review_event_id == event_id
                    )
                ) is not None:
                    continue
                policy = self._new_review_policy_skill(draft, submitted_by)
                session.add(policy)
                session.flush()
                session.add(
                    ReviewPolicySource(
                        policy_skill_id=policy.id,
                        review_event_id=event_id,
                    )
                )
                created.append(policy.id)
        return created

    def published_page(self, skill_id: int) -> dict:
        """按需读取一条仍处于发布状态的知识页面。"""

        with self.session_factory() as session:
            row = session.scalar(
                select(Skill).where(
                    Skill.id == skill_id,
                    Skill.status == "published",
                    ~self._review_policy_condition(),
                    or_(Skill.expires_at.is_(None), Skill.expires_at > self._now()),
                )
            )
            if row is None:
                raise LookupError("已发布 Skill 不存在")
            return self._public(row, {})

    def source(self, candidate_id: int) -> dict:
        """按候选保存的消息 ID 返回来源会话和精确原始消息，不推测历史缺失证据。"""

        with self.session_factory() as session:
            row = session.get(Skill, candidate_id)
            if row is None:
                raise ValueError("Skill 候选不存在")
            conversation = (
                session.get(Conversation, row.source_conversation_id)
                if row.source_conversation_id is not None
                else None
            )
            message_ids = [int(item) for item in (row.source_message_ids or [])]
            messages = []
            if conversation is not None and message_ids:
                messages = list(
                    session.scalars(
                        select(Message)
                        .where(
                            Message.conversation_id == conversation.id,
                            Message.id.in_(message_ids),
                        )
                        .order_by(Message.sent_at.asc(), Message.id.asc())
                    )
                )
            staff_ids = {item.sender_wecom_userid for item in messages if item.sender_wecom_userid}
            customer_ids = {
                item.sender_external_userid for item in messages if item.sender_external_userid
            }
            staff_names = {
                item.wecom_userid: item.name
                for item in session.scalars(select(User).where(User.wecom_userid.in_(staff_ids)))
            } if staff_ids else {}
            customer_names = {
                item.external_userid: item.name
                for item in session.scalars(
                    select(Customer).where(Customer.external_userid.in_(customer_ids))
                )
            } if customer_ids else {}
            return {
                "skill_id": row.id,
                "source_conversation_id": row.source_conversation_id,
                "conversation_name": conversation.name if conversation else "",
                "conversation_type": conversation.conversation_type if conversation else "",
                "evidence_available": bool(message_ids),
                "missing_message_count": max(len(message_ids) - len(messages), 0),
                "messages": [
                    {
                        "id": item.id,
                        "sender_kind": "staff" if item.sender_wecom_userid else "customer",
                        "sender_name": (
                            staff_names.get(item.sender_wecom_userid or "")
                            or customer_names.get(item.sender_external_userid or "")
                            or str((item.content_json or {}).get("sender_name") or "")
                            or item.sender_wecom_userid
                            or item.sender_external_userid
                            or "未知发送人"
                        ),
                        "content": item.content_text or "[非文本消息]",
                        "message_type": item.message_type,
                        "sent_at": item.sent_at.isoformat(),
                    }
                    for item in messages
                ],
            }

    @staticmethod
    def _snapshot(row: Skill) -> dict:
        """生成可 JSON 序列化的完整 Skill 审核快照。"""

        return {
            "id": row.id,
            "title": row.title,
            "content": row.content,
            "taxonomy_path": list(row.taxonomy_path or []),
            "status": row.status,
            "source_conversation_id": row.source_conversation_id,
            "ai_reply_id": row.ai_reply_id,
            "source_message_ids": [int(item) for item in (row.source_message_ids or [])],
            "expires_at": row.expires_at.isoformat() if row.expires_at else None,
            "version": int(row.version or 1),
        }

    def save_review(
        self,
        candidate_id: int,
        reviewer_userid: str,
        status: str,
        note: str,
        title: str,
        content: str,
        taxonomy_path: list[dict[str, str]],
        expires_at: datetime | None = None,
        *,
        expected_version: int,
        action: str,
        reason_codes: list[str],
        governance_snapshot: dict | None = None,
        request_id: str | None = None,
        policy_draft: dict | None = None,
    ) -> dict:
        """在同一事务中持久化审核结果并追加不可变事件。

        Raises:
            ValueError: 指定 Skill 候选不存在。
            ConcurrencyConflictError: 当前版本与请求版本不一致。
        """
        with self.session_factory.begin() as session:
            row = session.scalar(
                select(Skill).where(Skill.id == candidate_id).with_for_update()
            )
            if row is None:
                raise ValueError("Skill 候选不存在")
            current_version = int(row.version or 1)
            if current_version != int(expected_version):
                raise ConcurrencyConflictError(
                    f"Skill 已被其他审核员修改，当前版本为 {current_version}，请刷新后重试"
                )
            before_snapshot = self._snapshot(row)
            if title.strip():
                row.title = title.strip()[:255]
            if content.strip():
                row.content = content.strip()
            row.taxonomy_path = taxonomy_path
            row.status = status
            row.reviewer_userid = reviewer_userid
            row.review_opinion = note.strip() or None
            row.expires_at = expires_at
            row.version = current_version + 1
            session.flush()
            after_snapshot = self._snapshot(row)
            event = SkillReviewEvent(
                skill_id=row.id,
                reviewer_userid=reviewer_userid,
                action=action,
                reason_codes=list(dict.fromkeys(reason_codes)),
                opinion=note.strip(),
                before_snapshot=before_snapshot,
                after_snapshot=after_snapshot,
                governance_snapshot=dict(governance_snapshot or {}),
                request_id=request_id or uuid4().hex,
            )
            session.add(event)
            session.flush()
            policy_skill_id = None
            if policy_draft and not self._is_review_policy(before_snapshot["taxonomy_path"]):
                policy = self._new_review_policy_skill(policy_draft, reviewer_userid)
                session.add(policy)
                session.flush()
                session.add(
                    ReviewPolicySource(
                        policy_skill_id=policy.id,
                        review_event_id=event.id,
                    )
                )
                policy_skill_id = policy.id
            is_review_policy = self._is_review_policy(row.taxonomy_path)
            if (
                not is_review_policy
                and (before_snapshot["status"] == "published" or row.status == "published")
            ):
                session.add(SkillIndexJob(
                    skill_id=row.id,
                    operation="upsert" if row.status == "published" else "delete",
                    status="pending",
                ))
            session.flush()
            result = self._public(row, {reviewer_userid: reviewer_userid})
            result["review_event_id"] = event.id
            result["review_policy_draft_id"] = policy_skill_id
            return result

    @staticmethod
    def _is_review_policy(path: list[dict] | None) -> bool:
        """判断分类路径是否属于只供知识提炼使用的系统审核规则。"""

        return bool(path and path[0].get("code") == "review_policy")

    @staticmethod
    def _new_review_policy_skill(draft: dict, submitted_by: str) -> Skill:
        """把 Service 已校验的规则草案转换为待人工确认的 Skill 实体。"""

        return Skill(
            source_message_ids=[],
            title=str(draft["title"]).strip()[:255],
            content=str(draft["content"]).strip(),
            taxonomy_path=list(draft["taxonomy_path"]),
            status="pending",
            submitted_by=submitted_by,
            review_opinion="由审核结果生成的规则草案，发布前必须人工确认",
            version=1,
        )

    @staticmethod
    def _public(row: Skill, users: dict[str, str]) -> dict:
        """把 ORM 实体转换成审核台可安全展示的字段集合。"""
        return {
            "id": row.id, "title": row.title, "content": row.content,
            "taxonomy_path": list(row.taxonomy_path or []),
            "quality_score": float(row.quality_score or 0), "status": row.status,
            "source_conversation_id": row.source_conversation_id,
            "source_message_count": len(row.source_message_ids or []),
            "has_source_evidence": bool(row.source_message_ids),
            "submitted_by": row.submitted_by or "",
            "submitted_by_name": users.get(row.submitted_by or "", row.submitted_by or ""),
            "reviewer_userid": row.reviewer_userid or "",
            "review_opinion": row.review_opinion or "",
            "version": int(row.version or 1),
            "expires_at": row.expires_at.isoformat() if row.expires_at else None,
            "is_expired": bool(row.expires_at and row.expires_at <= SkillRepository._now()),
        }

"""Skill 候选评分、权限校验、审核与发布业务。"""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher

from backend.config import Settings
from backend.database.repositories.skill_repository import SkillRepository
from backend.schemas.ai import ReplyRequest
from backend.schemas.identifiers import parse_public_conversation_id
from backend.schemas.skill_taxonomy import (
    BUSINESS_KNOWLEDGE_DOMAINS,
    build_taxonomy_tree,
    normalize_taxonomy_path,
    taxonomy_matches,
)
from backend.schemas.yuque_taxonomy import yuque_catalog_tree

PRODUCT_MODEL_PATTERN = re.compile(r"\b[A-Z]{1,8}[-_]?\d[A-Z0-9._-]*\b", re.IGNORECASE)
RISKY_COMMITMENT_PATTERN = re.compile(r"(保证|一定|百分之百|绝对|永久|当天必到)")
REVIEW_REASON_CODES = {
    "insufficient_evidence",
    "not_generalizable",
    "contains_private_context",
    "factual_risk",
    "wrong_taxonomy",
    "duplicate",
    "conflict",
    "outdated",
    "poor_expression",
    "evidence_complete",
    "reusable",
    "taxonomy_confirmed",
    "other",
}
REVIEW_REASON_RULES = {
    "insufficient_evidence": ("需要补证据", "缺少可追溯原始消息证据时，不创建可发布候选；先补齐精确证据。"),
    "not_generalizable": ("不应获取", "只适用于单个客户、项目或会话临时状态的内容不得沉淀为通用知识。"),
    "contains_private_context": ("不应获取", "包含客户身份、群名、订单或项目专属上下文的内容必须先脱敏。"),
    "factual_risk": ("需要补证据", "存在事实风险的产品结论必须补充目标型号原文或可靠审核证据。"),
    "wrong_taxonomy": ("需要补证据", "分类路径无法确认时不得发布，必须先核对受控知识域和产品层级。"),
    "duplicate": ("不应获取", "与同一分类路径内已发布知识重复的内容不得重复沉淀。"),
    "conflict": ("需要补证据", "与已发布知识冲突时必须保留冲突并补证据，由人工确认后再处理。"),
    "outdated": ("不应获取", "已经过期或不再适用的内容不得继续作为新知识沉淀。"),
    "poor_expression": ("需要补证据", "表达不完整或不可执行的内容应先修订到边界清晰，再进入发布审核。"),
    "evidence_complete": ("应获取", "优先沉淀具有精确原始消息证据且证据链完整的通用知识。"),
    "reusable": ("应获取", "优先沉淀去除客户和项目特定信息后仍可跨会话复用的知识。"),
    "taxonomy_confirmed": ("应获取", "分类路径已经按受控知识域和产品层级确认的候选可进入发布审核。"),
    "other": ("需要补证据", "按人工审核意见补充边界与证据后，再决定是否沉淀。"),
}


def quality_score(question: str, answer: str, product_model: str, evidence_refs: list[dict], conflict_details: list[dict] | None = None) -> tuple[float, dict]:
    """使用透明规则计算候选初评分；分数仅用于排序，不能自动发布。"""
    checks = {
        "question_complete": len(question.strip()) >= 8,
        "answer_complete": len(answer.strip()) >= 12,
        "product_identified": bool(product_model.strip()),
        "has_evidence": bool(evidence_refs),
        "avoids_unverified_commitment": not bool(RISKY_COMMITMENT_PATTERN.search(answer)),
    }
    weights = {"question_complete": 25, "answer_complete": 30, "product_identified": 15, "has_evidence": 20, "avoids_unverified_commitment": 10}
    penalty = min(20.0, 8.0 * len(conflict_details or []))
    score = max(0.0, float(sum(weights[key] for key, passed in checks.items() if passed)) - penalty)
    return score, {"checks": checks, "weights": weights, "conflict_penalty": penalty}


class SkillService:
    """编排 Skill 候选提交、审核权限、编辑、驳回与人工发布。"""

    def __init__(self, repository: SkillRepository, settings: Settings):
        """注入纯数据仓储和集中配置。"""
        self.repository = repository
        self.settings = settings

    def _reviewer(self, staff_userid: str) -> dict:
        """校验 reviewer/admin 权限并返回当前审核人员信息。"""
        user = self.repository.get_user(staff_userid)
        if user is None:
            raise PermissionError("当前员工尚未同步到客服宝数据库")
        allowed = user["is_enabled"] and (user["role"] in {"reviewer", "admin"} or staff_userid in self.settings.reviewer_userids)
        if not allowed:
            raise PermissionError("当前账号没有 Skill 审核权限")
        return user

    def knowledge_domains(self, staff_userid: str) -> dict:
        """返回分类 Schema 中的唯一知识域定义及客服回答可用范围。"""

        self._reviewer(staff_userid)
        return {
            "items": [
                {
                    "code": code,
                    "name": name,
                    "customer_answer_enabled": code in BUSINESS_KNOWLEDGE_DOMAINS,
                }
                # 系统审核规则只能由审核结果编译并在专用页面操作，不能让
                # 普通业务候选通过分类下拉框伪装成 review_policy。
                for code, name in BUSINESS_KNOWLEDGE_DOMAINS.items()
            ]
        }

    def knowledge_catalog(self, staff_userid: str) -> dict:
        """返回专业人员确认的语雀产品目录骨架，供审核分类和 AI 路由复用。"""

        self._reviewer(staff_userid)
        return {"items": yuque_catalog_tree()}

    def submit_candidate(self, request: ReplyRequest, suggestion_id: int, answer_text: str, product_model: str = "", taxonomy_path: list[dict[str, str]] | None = None) -> dict:
        """把客服采纳的 AI 回复提交为待人工审核 Skill 候选。"""
        answer = answer_text.strip()
        if not answer:
            raise ValueError("候选内容不能为空")
        conversation_id = parse_public_conversation_id(request.public_conversation_id)
        reply = self.repository.get_reply(suggestion_id, conversation_id, request.staff_userid)
        if reply is None:
            raise PermissionError("回复建议不存在或不属于当前客服")
        model = product_model.strip()
        if not model:
            match = PRODUCT_MODEL_PATTERN.search(f"{reply['question_summary']}\n{answer}")
            model = match.group(0).upper() if match else ""
        score, _ = quality_score(reply["question_summary"], answer, model, [])
        normalized_path = normalize_taxonomy_path(taxonomy_path or [])
        return self.repository.save_candidate(
            suggestion_id,
            conversation_id,
            list(reply["context_message_ids"]),
            reply["question_summary"],
            answer,
            score,
            request.staff_userid,
            normalized_path,
        )

    def list_candidates(
        self,
        staff_userid: str,
        status: str,
        taxonomy_filters: dict[str, str],
        keyword: str,
        page: int,
        page_size: int,
    ) -> dict:
        """校验审核权限后分页返回轻量候选列表。"""
        reviewer = self._reviewer(staff_userid)
        rows, total = self.repository.list_review_pages(
            status, taxonomy_filters, keyword, page, page_size
        )
        items = [
            {
                "id": item["id"],
                "title": item["title"],
                "status": item["status"],
                "quality_score": item["quality_score"],
                "taxonomy_path": item["taxonomy_path"],
                "source_message_count": item["source_message_count"],
            }
            for item in rows
        ]
        return {
            "reviewer": reviewer,
            "items": items,
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": math.ceil(total / page_size) if total else 0,
        }

    @staticmethod
    def _normalized_text(value: str) -> str:
        """移除标点和空白后比较中文知识正文，避免格式差异绕过去重。"""

        return re.sub(r"[^\w]+", "", value.casefold())

    def _governance(self, candidate_id: int, title: str, content: str, path: list[dict[str, str]]) -> dict:
        """在相同叶子分类内识别重复与潜在冲突，不替代人工判断。"""

        title_key = self._normalized_text(title)
        content_key = self._normalized_text(content)
        duplicates, conflicts = [], []
        for item in self.repository.published_in_path(path, candidate_id):
            existing_title = self._normalized_text(item["title"])
            existing_content = self._normalized_text(item["content"])
            similarity = SequenceMatcher(None, content_key, existing_content).ratio()
            if (
                content_key == existing_content
                or (title_key == existing_title and similarity >= 0.8)
                or similarity >= 0.92
            ):
                duplicates.append({"skill_id": item["id"], "title": item["title"], "similarity": round(similarity, 3)})
            else:
                conflicts.append({"skill_id": item["id"], "title": item["title"], "similarity": round(similarity, 3)})
        return {"duplicates": duplicates, "conflicts": conflicts}

    @staticmethod
    def _naive_utc(value: datetime | None) -> datetime | None:
        """把 API 日期统一转换为 MySQL DATETIME 使用的无时区 UTC。"""

        if value is None:
            return None
        if value.tzinfo is None:
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def candidate_detail(self, staff_userid: str, candidate_id: int) -> dict:
        """返回审核工作区选中的单条候选详情。"""

        self._reviewer(staff_userid)
        detail = self.repository.review_detail(candidate_id)
        detail["governance"] = self._governance(
            candidate_id, detail["title"], detail["content"], detail["taxonomy_path"]
        ) if detail["taxonomy_path"] else {"duplicates": [], "conflicts": []}
        return detail

    def review_events(self, staff_userid: str, candidate_id: int) -> dict:
        """返回审核人员可见的不可变审核时间线。"""

        self._reviewer(staff_userid)
        return {"skill_id": candidate_id, "items": self.repository.review_events(candidate_id)}

    @staticmethod
    def _reason_codes(values: list[str], *, required: bool = False) -> list[str]:
        """规范化结构化审核原因，并拒绝前后端枚举漂移。"""

        result = list(dict.fromkeys(str(item).strip() for item in values if str(item).strip()))
        unknown = [item for item in result if item not in REVIEW_REASON_CODES]
        if unknown:
            raise ValueError(f"不支持的审核原因：{'、'.join(unknown)}")
        if required and not result:
            raise ValueError("驳回或归档必须选择结构化审核原因")
        return result

    @staticmethod
    def _assert_transition(current_status: str, target_status: str) -> None:
        """校验候选终态软删除和已发布知识归档状态机。

        归档知识允许原地修订，也允许再次软删除为 ``rejected``。后者只是在
        审核台隐藏归档结果，数据库中的 Skill、来源证据和审核事件仍完整保留。
        """

        allowed = {
            "pending": {"published", "rejected"},
            "published": {"published", "archived"},
            "archived": {"archived", "pending", "rejected"},
            "rejected": set(),
        }
        if target_status not in allowed.get(current_status, set()):
            raise ValueError(f"不允许从 {current_status} 变更为 {target_status}")

    @staticmethod
    def _review_policy_draft(
        reviewed_skill: dict,
        action: str,
        reason_codes: list[str],
        note: str,
    ) -> dict:
        """把一次业务 Skill 通过/驳回转换为待人工确认的审核规则 Skill。

        这里只使用结构化原因做确定性编译，不让模型把单次意见直接泛化成生产
        规则。草案发布前仍可编辑，只有 admin 的明确发布动作才会影响后续候选
        提炼；原始候选标题和完整审核事件通过关联表查看，不写入规则正文。
        """

        rules = [REVIEW_REASON_RULES[code] for code in reason_codes if code in REVIEW_REASON_RULES]
        if action == "publish":
            conclusion = "应获取"
            statements = [text for kind, text in rules if kind == "应获取"]
            if not statements:
                statements = ["具备可追溯来源、可跨会话复用且分类路径经人工确认的候选可以进入知识发布。"]
            topic_code, topic_name = "extract_allow", "应获取规则"
        else:
            evidence_rules = [text for kind, text in rules if kind == "需要补证据"]
            deny_rules = [text for kind, text in rules if kind == "不应获取"]
            if evidence_rules:
                conclusion = "需要补证据"
                statements = evidence_rules + deny_rules
                topic_code, topic_name = "evidence_required", "补证据规则"
            else:
                conclusion = "不应获取"
                statements = deny_rules or ["候选未通过人工审核；必须先确认不可沉淀边界后再形成长期规则。"]
                topic_code, topic_name = "extract_deny", "不应获取规则"
        unique_statements = list(dict.fromkeys(statements))
        content_lines = [
            f"规则结论：{conclusion}",
            "适用阶段：通用知识候选提炼",
            "规则建议：",
            *[f"- {item}" for item in unique_statements],
        ]
        if note.strip():
            content_lines.extend(["人工审核补充（发布前需确认已脱敏）：", note.strip()])
        content_lines.append("边界：本草案只影响候选是否创建和证据要求，不得自动通过、驳回或发布任何候选。")
        title = f"{conclusion}：{str(reviewed_skill.get('title') or '审核候选').strip()}"
        return {
            "title": title[:255],
            "content": "\n".join(content_lines),
            "taxonomy_path": [
                {"level": "knowledge_domain", "code": "review_policy", "name": "系统审核规则"},
                {"level": "topic", "code": topic_code, "name": topic_name},
            ],
        }

    def review(
        self,
        staff_userid: str,
        candidate_id: int,
        decision: str,
        note: str = "",
        title: str = "",
        content: str = "",
        taxonomy_path: list[dict[str, str]] | None = None,
        expires_at: datetime | None = None,
        expected_version: int = 1,
        reason_codes: list[str] | None = None,
    ) -> dict:
        """保存候选编辑结果，或将候选驳回；发布必须走独立方法。"""
        reviewer = self._reviewer(staff_userid)
        if decision not in {"pending", "rejected", "published", "archived"}:
            raise ValueError("不支持的 Skill 状态")
        if decision == "published":
            return self.publish(
                staff_userid, candidate_id, note, title, content, taxonomy_path,
                expires_at, expected_version, reason_codes,
            )
        current = self.repository.review_detail(candidate_id)
        if current.get("taxonomy_path") and current["taxonomy_path"][0].get("code") == "review_policy":
            raise ValueError("系统审核规则必须通过审核规则专用接口操作")
        self._assert_transition(current["status"], decision)
        if current["status"] == "archived" and decision == "pending" and reviewer["role"] != "admin":
            raise PermissionError("只有管理员可以申请恢复已归档 Skill，恢复后仍需重新审核")
        reasons = self._reason_codes(
            reason_codes or [], required=decision in {"rejected", "archived"}
        )
        if {"other", "conflict"} & set(reasons) and not note.strip():
            raise ValueError("选择其他或知识冲突原因时必须填写具体审核意见")
        normalized_path = normalize_taxonomy_path(
            taxonomy_path or current.get("taxonomy_path") or [],
            require_publishable=decision == "published",
        )
        if normalized_path and normalized_path[0]["code"] == "review_policy":
            raise ValueError("业务 Skill 不能修改为系统审核规则")
        effective_title = title.strip() or current["title"]
        effective_content = content.strip() or current["content"]
        governance = (
            self._governance(
                candidate_id, effective_title, effective_content, normalized_path
            )
            if normalized_path
            else {"duplicates": [], "conflicts": []}
        )
        # 同一个目标状态在不同来源状态下含义不同，事件动作必须明确区分，
        # 便于效果看板和审核历史判断是候选驳回、归档删除还是归档内容修订。
        if current["status"] == "archived" and decision == "rejected":
            action = "delete_archived"
        elif current["status"] == "archived" and decision == "archived":
            action = "edit_archived"
        else:
            action = {"rejected": "reject", "archived": "archive", "pending": "reopen"}[decision]
        policy_draft = (
            self._review_policy_draft(current, action, reasons, note)
            if current["status"] == "pending" and action == "reject"
            else None
        )
        return self.repository.save_review(
            candidate_id, staff_userid, decision, note, title, content,
            normalized_path, self._naive_utc(expires_at),
            expected_version=expected_version,
            action=action,
            reason_codes=reasons,
            governance_snapshot={
                **governance,
                "evidence_available": current.get("has_source_evidence", False),
            },
            policy_draft=policy_draft,
        )

    def source(self, staff_userid: str, candidate_id: int) -> dict:
        """校验审核权限后返回候选的来源会话和精确原始消息。"""

        self._reviewer(staff_userid)
        return self.repository.source(candidate_id)

    def publish(
        self,
        staff_userid: str,
        candidate_id: int,
        note: str = "",
        title: str = "",
        content: str = "",
        taxonomy_path: list[dict[str, str]] | None = None,
        expires_at: datetime | None = None,
        expected_version: int = 1,
        reason_codes: list[str] | None = None,
    ) -> dict:
        """由有权限人员确认内容后发布正式 Skill。"""
        self._reviewer(staff_userid)
        normalized_path = normalize_taxonomy_path(
            taxonomy_path or [], require_publishable=True
        )
        if normalized_path[0]["code"] == "review_policy":
            raise ValueError("业务 Skill 不能通过普通发布接口进入系统审核规则")
        current = self.repository.review_detail(candidate_id)
        if current.get("taxonomy_path") and current["taxonomy_path"][0].get("code") == "review_policy":
            raise ValueError("系统审核规则必须通过审核规则专用接口操作")
        self._assert_transition(current["status"], "published")
        if current.get("has_source_evidence") is False:
            raise ValueError("候选缺少精确原始消息证据，不能发布")
        effective_title = title.strip() or current["title"]
        effective_content = content.strip() or current["content"]
        governance = self._governance(candidate_id, effective_title, effective_content, normalized_path)
        if governance["duplicates"]:
            ids = "、".join(f"skill:{item['skill_id']}" for item in governance["duplicates"])
            raise ValueError(f"检测到重复知识 {ids}，请合并或归档后再发布")
        if governance["conflicts"] and not note.strip():
            ids = "、".join(f"skill:{item['skill_id']}" for item in governance["conflicts"])
            raise ValueError(f"检测到同主题潜在冲突 {ids}，请填写审核意见确认差异")
        reasons = self._reason_codes(reason_codes or [])
        result = self.repository.save_review(
            candidate_id, staff_userid, "published", note, effective_title,
            effective_content, normalized_path, self._naive_utc(expires_at),
            expected_version=expected_version,
            action="edit" if current["status"] == "published" else "publish",
            reason_codes=reasons,
            governance_snapshot={
                **governance,
                "evidence_available": current.get("has_source_evidence", False),
            },
            policy_draft=(
                self._review_policy_draft(current, "publish", reasons, note)
                if current["status"] == "pending"
                else None
            ),
        )
        result["governance"] = governance
        return result

    def taxonomy_tree(self, staff_userid: str) -> dict:
        """返回由已发布 Skill 分类路径实时聚合的知识目录树。"""

        self._reviewer(staff_userid)
        items = self.repository.list_published()
        return {
            "tree": build_taxonomy_tree(items, include_skills=False),
            "skill_count": len(items),
        }

    def wiki_pages(
        self,
        staff_userid: str,
        taxonomy_filters: dict[str, str],
        keyword: str,
        page: int,
        page_size: int,
    ) -> dict:
        """按分类路径和关键词分页返回知识体系叶子页面。"""

        self._reviewer(staff_userid)
        rows, total = self.repository.list_published_pages(
            taxonomy_filters,
            keyword,
            page,
            page_size,
        )
        items = [
            {
                "id": item["id"],
                "title": item["title"],
                "taxonomy_path": item["taxonomy_path"],
                "quality_score": item["quality_score"],
            }
            for item in rows
        ]
        return {
            "items": items,
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": math.ceil(total / page_size) if total else 0,
        }

    def wiki_page(self, staff_userid: str, skill_id: int) -> dict:
        """返回知识工作区当前选中的一条已发布 Skill。"""

        self._reviewer(staff_userid)
        return self.repository.published_page(skill_id)

    def compile_review_policy_drafts(self, staff_userid: str) -> dict:
        """幂等补编译历史通过/驳回事件，供新功能上线后补齐旧数据。"""

        reviewer = self._reviewer(staff_userid)
        source_events = self.repository.unlinked_review_decisions()
        drafts = []
        for event in source_events:
            draft = self._review_policy_draft(
                event,
                event["action"],
                list(event.get("reason_codes") or []),
                str(event.get("opinion") or ""),
            )
            drafts.append({**draft, "review_event_id": event["event_id"]})
        created_ids = self.repository.create_review_policy_drafts(
            drafts, reviewer["wecom_userid"]
        )
        return {
            "scanned": len(source_events),
            "created": len(created_ids),
            "skill_ids": created_ids,
        }

    def review_policy_rules(
        self,
        staff_userid: str,
        status: str,
        keyword: str,
        page: int,
        page_size: int,
    ) -> dict:
        """分页返回审核规则 Skill 的轻量列表。"""

        self._reviewer(staff_userid)
        if status not in {"", "pending", "published", "rejected", "archived"}:
            raise ValueError("不支持的审核规则状态")
        rows, total = self.repository.list_review_policy_rules(
            status, keyword, page, page_size
        )
        return {
            "items": [
                {
                    "id": item["id"],
                    "title": item["title"],
                    "status": item["status"],
                    "taxonomy_path": item["taxonomy_path"],
                    "version": item["version"],
                }
                for item in rows
            ],
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": math.ceil(total / page_size) if total else 0,
        }

    def review_policy_detail(self, staff_userid: str, policy_skill_id: int) -> dict:
        """返回规则正文、状态和产生该规则的原始审核事件。"""

        self._reviewer(staff_userid)
        return self.repository.review_policy_detail(policy_skill_id)

    def update_review_policy(
        self,
        staff_userid: str,
        policy_skill_id: int,
        decision: str,
        title: str,
        content: str,
        note: str,
        expected_version: int,
        reason_codes: list[str] | None = None,
    ) -> dict:
        """编辑或人工发布审核规则；已发布规则的变更仅允许 admin。"""

        reviewer = self._reviewer(staff_userid)
        current = self.repository.review_policy_detail(policy_skill_id)
        allowed = {
            "pending": {"pending", "published", "rejected"},
            "published": {"published", "archived"},
            "archived": {"pending"},
            "rejected": set(),
        }
        if decision not in allowed.get(current["status"], set()):
            raise ValueError(
                f"不允许从 {current['status']} 变更为 {decision}"
            )
        if (
            decision in {"published", "archived"}
            or current["status"] in {"published", "archived"}
        ) and reviewer["role"] != "admin":
            raise PermissionError("只有管理员可以发布、修改、归档或恢复系统审核规则")
        effective_title = title.strip() or current["title"]
        effective_content = content.strip() or current["content"]
        if not effective_title or not effective_content:
            raise ValueError("审核规则标题和正文不能为空")
        reasons = self._reason_codes(
            reason_codes or [], required=decision in {"rejected", "archived"}
        )
        if {"other", "conflict"} & set(reasons) and not note.strip():
            raise ValueError("选择其他或知识冲突原因时必须填写具体审核意见")
        action = {
            ("pending", "pending"): "edit_policy_draft",
            ("pending", "published"): "publish_policy",
            ("pending", "rejected"): "reject_policy_draft",
            ("published", "published"): "edit_policy",
            ("published", "archived"): "archive_policy",
            ("archived", "pending"): "reopen_policy",
        }[(current["status"], decision)]
        return self.repository.save_review(
            policy_skill_id,
            staff_userid,
            decision,
            note,
            effective_title,
            effective_content,
            current["taxonomy_path"],
            None,
            expected_version=expected_version,
            action=action,
            reason_codes=reasons,
            governance_snapshot={
                "source_review_event_ids": [
                    item["id"] for item in current["source_review_events"]
                ],
                "customer_answer_enabled": False,
            },
        )

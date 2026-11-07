"""AI 回复、会话摘要和 Skill 审核接口。"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.database.session import get_db
from backend.config import get_settings
from backend.database.repositories.skill_repository import SkillRepository
from backend.database.repositories.summary_repository import SummaryRepository
from backend.database.repositories.effect_repository import EffectRepository
from backend.dependencies.auth import PasswordReadyUser
from backend.integrations.minimax import OpenAICompatibleChatClient
from backend.schemas.ai import ReplyRequest
from backend.schemas.requests import (
    ReplyActionRequest,
    ReplyGenerateRequest,
    SkillReviewRequest,
    SkillSubmitRequest,
)
from backend.services.summary_service import ConversationSummaryService
from backend.services.ai_reply_service import build_ai_reply_service
from backend.services.skill_service import SkillService
from backend.services.effect_service import EffectService


router = APIRouter(tags=["ai"])


@lru_cache(maxsize=1)
def reply_service():
    """构造并缓存无请求状态的 AI 服务。"""

    return build_ai_reply_service(get_settings())


@lru_cache(maxsize=1)
def skill_service():
    """构造并缓存 Skill 审核服务。"""

    return SkillService(SkillRepository(), get_settings())


@lru_cache(maxsize=1)
def effect_service():
    """构造只读效果指标服务。"""

    return EffectService(EffectRepository())


def request_for(conversation_id: int, staff_userid: str) -> ReplyRequest:
    """构造 AI 服务使用的稳定领域请求。"""

    return ReplyRequest(
        public_conversation_id=f"conv_{conversation_id}",
        staff_userid=staff_userid,
    )


@router.get("/api/conversations/{conversation_id}/ai-replies/latest")
def latest_reply(conversation_id: int, user: PasswordReadyUser):
    """连接会话后读取最近保存的 AI 建议，不自动调用模型。"""

    return reply_service().latest(request_for(conversation_id, user["wecom_userid"]))


@router.post("/api/conversations/{conversation_id}/ai-replies")
def generate_reply(
    conversation_id: int,
    body: ReplyGenerateRequest,
    user: PasswordReadyUser,
):
    """人工触发问题提取、知识检索和回复生成。"""

    request = request_for(conversation_id, user["wecom_userid"])
    request = ReplyRequest(
        public_conversation_id=request.public_conversation_id,
        staff_userid=request.staff_userid,
        regenerate=body.regenerate,
    )
    return reply_service().generate(request)


@router.patch("/api/ai-replies/{reply_id}")
def update_reply(
    reply_id: int,
    body: ReplyActionRequest,
    user: PasswordReadyUser,
):
    """设计书定义的 AI 回复状态更新接口。"""

    return reply_service().record_action(
        user["wecom_userid"], suggestion_id=reply_id,
        action=body.action,
        edited_text=body.edited_text,
    )


@router.post("/api/admin/skills")
def submit_skill(body: SkillSubmitRequest, user: PasswordReadyUser):
    """将采纳回复提交为待审核 Skill 候选。"""

    return skill_service().submit_candidate(
        request_for(body.conversation_id, user["wecom_userid"]),
        suggestion_id=body.suggestion_id,
        answer_text=body.answer_text,
        product_model=body.product_model,
        taxonomy_path=body.taxonomy_path,
    )


@router.get("/api/admin/skills")
def skill_list(
    user: PasswordReadyUser,
    status: str = Query(default=""),
    keyword: str = Query(default="", max_length=255),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, ge=1, le=100),
    knowledge_domain: str = Query(default=""),
    product_category: str = Query(default=""),
    product_subcategory: str = Query(default=""),
    product_model: str = Query(default=""),
    topic: str = Query(default=""),
):
    """设计书定义的 Skill 列表接口。"""

    return skill_service().list_candidates(
        user["wecom_userid"],
        status,
        {
            "knowledge_domain": knowledge_domain,
            "product_category": product_category,
            "product_subcategory": product_subcategory,
            "product_model": product_model,
            "topic": topic,
        },
        keyword,
        page,
        page_size,
    )


@router.get("/api/admin/skill-taxonomy/domains")
def skill_knowledge_domains(user: PasswordReadyUser):
    """返回审核台与后端校验共用的受控知识域定义。"""

    return skill_service().knowledge_domains(user["wecom_userid"])


@router.get("/api/admin/skill-taxonomy/catalog")
def skill_knowledge_catalog(user: PasswordReadyUser):
    """返回按语雀专业目录整理的产品分类骨架。"""

    return skill_service().knowledge_catalog(user["wecom_userid"])


@router.get("/api/admin/skills/{skill_id}")
def skill_detail(skill_id: int, user: PasswordReadyUser):
    """按需读取审核工作区当前选中的候选详情。"""

    return skill_service().candidate_detail(user["wecom_userid"], skill_id)


@router.get("/api/admin/skill-wiki/tree")
def skill_wiki_tree(user: PasswordReadyUser):
    """返回由已发布 Skill 实时聚合的五级知识目录树。"""

    return skill_service().taxonomy_tree(user["wecom_userid"])


@router.get("/api/admin/skill-wiki/pages")
def skill_wiki_pages(
    user: PasswordReadyUser,
    keyword: str = Query(default="", max_length=255),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, ge=1, le=100),
    knowledge_domain: str = Query(default=""),
    product_category: str = Query(default=""),
    product_subcategory: str = Query(default=""),
    product_model: str = Query(default=""),
    topic: str = Query(default=""),
):
    """按分类和关键词分页查询知识页面，禁止无界返回全部正文。"""

    return skill_service().wiki_pages(
        user["wecom_userid"],
        {
            "knowledge_domain": knowledge_domain,
            "product_category": product_category,
            "product_subcategory": product_subcategory,
            "product_model": product_model,
            "topic": topic,
        },
        keyword,
        page,
        page_size,
    )


@router.get("/api/admin/skill-wiki/pages/{skill_id}")
def skill_wiki_page(skill_id: int, user: PasswordReadyUser):
    """按需读取知识工作区选中的单条 Skill 详情。"""

    return skill_service().wiki_page(user["wecom_userid"], skill_id)


@router.post("/api/admin/review-policy/compile")
def compile_review_policy(user: PasswordReadyUser):
    """幂等补齐历史通过/驳回事件对应的审核规则 Skill 草案。"""

    return skill_service().compile_review_policy_drafts(user["wecom_userid"])


@router.get("/api/admin/review-policy/rules")
def review_policy_rules(
    user: PasswordReadyUser,
    status: str = Query(default="pending"),
    keyword: str = Query(default="", max_length=255),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, ge=1, le=100),
):
    """分页返回审核结果生成的系统审核规则 Skill。"""

    return skill_service().review_policy_rules(
        user["wecom_userid"], status, keyword, page, page_size
    )


@router.get("/api/admin/review-policy/rules/{policy_skill_id}")
def review_policy_detail(policy_skill_id: int, user: PasswordReadyUser):
    """返回一条审核规则及其原始审核事件证据。"""

    return skill_service().review_policy_detail(
        user["wecom_userid"], policy_skill_id
    )


@router.patch("/api/admin/review-policy/rules/{policy_skill_id}")
def update_review_policy(
    policy_skill_id: int,
    body: SkillReviewRequest,
    user: PasswordReadyUser,
):
    """编辑、发布、驳回、归档或恢复系统审核规则 Skill。"""

    return skill_service().update_review_policy(
        user["wecom_userid"],
        policy_skill_id,
        body.decision,
        body.question_text,
        body.answer_text,
        body.note,
        body.expected_version,
        body.reason_codes,
    )


@router.get("/api/admin/skills/{skill_id}/source")
def skill_source(skill_id: int, user: PasswordReadyUser):
    """返回 Skill 候选对应的来源会话和精确原始消息证据。"""

    return skill_service().source(user["wecom_userid"], skill_id)


@router.get("/api/admin/skills/{skill_id}/review-events")
def skill_review_events(skill_id: int, user: PasswordReadyUser):
    """返回 Skill 的完整不可变审核时间线。"""

    return skill_service().review_events(user["wecom_userid"], skill_id)


@router.get("/api/admin/metrics/overview")
def effect_overview(
    user: PasswordReadyUser,
    days: int = Query(default=30, ge=1, le=365),
):
    """返回审核、检索和回复操作的基础效果闭环指标。"""

    return effect_service().overview(user["wecom_userid"], days)


@router.patch("/api/admin/skills/{skill_id}")
def review_skill(skill_id: int, body: SkillReviewRequest, user: PasswordReadyUser):
    """设计书定义的 Skill 编辑、审核和发布接口。"""

    return skill_service().review(
        user["wecom_userid"],
        skill_id,
        body.decision,
        body.note,
        body.question_text,
        body.answer_text,
        body.taxonomy_path,
        body.expires_at,
        body.expected_version,
        body.reason_codes,
    )


@router.post("/api/admin/skills/{skill_id}/publish")
def publish_skill(skill_id: int, body: SkillReviewRequest, user: PasswordReadyUser):
    """设计书定义的 Skill 人工发布接口。"""

    return skill_service().publish(
        user["wecom_userid"], skill_id, body.note, body.question_text, body.answer_text,
        body.taxonomy_path,
        body.expires_at,
        body.expected_version,
        body.reason_codes,
    )


def summary_service(session: Session) -> ConversationSummaryService:
    """构造请求级会话摘要服务。"""

    settings = get_settings()
    model = OpenAICompatibleChatClient(
        base_url=settings.ai_model_base_url,
        api_key=settings.ai_model_api_key,
        model_name=settings.ai_model_name,
        timeout_seconds=settings.ai_model_timeout_seconds,
    )
    return ConversationSummaryService(SummaryRepository(session), model)


@router.get("/api/conversations/{conversation_id}/summary")
def get_summary(
    conversation_id: int,
    user: PasswordReadyUser,
    session: Annotated[Session, Depends(get_db)],
):
    """查询当前单聊或群聊的滚动摘要，不触发模型。"""

    return summary_service(session).get(conversation_id, user["wecom_userid"])


@router.post("/api/conversations/{conversation_id}/summary")
def update_summary(
    conversation_id: int,
    user: PasswordReadyUser,
    session: Annotated[Session, Depends(get_db)],
):
    """人工触发当前单聊或群聊的增量摘要，作为后台任务的补偿入口。"""

    return summary_service(session).update(conversation_id, user["wecom_userid"])

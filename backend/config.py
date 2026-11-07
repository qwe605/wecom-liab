"""集中读取客服宝运行配置。

所有密钥和连接串只从环境变量读取。默认值仅用于本地开发，生产环境必须覆盖
`APP_SECRET`、企业微信凭据和数据库密码。
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from functools import lru_cache


DEFAULT_DATABASE_URL = (
    "mysql+pymysql://kefubao:kefubao_local_dev@127.0.0.1:33069/"
    "kefubao?charset=utf8mb4"
)

# 本地未配置时只使用进程级随机签名密钥；绝不能使用公开的固定密钥签发令牌。
_LOCAL_APP_SECRET = secrets.token_urlsafe(48)


@dataclass(frozen=True)
class Settings:
    """应用配置快照。

    配置对象不可变，避免请求处理中意外修改全局配置。测试可以通过清除
    `get_settings` 缓存并设置环境变量构造独立配置。
    """

    app_name: str
    host: str
    port: int
    app_secret: str
    database_url: str
    wecom_corp_id: str
    wecom_agent_id: str
    wecom_corp_secret: str
    wecom_contacts_secret: str
    wecom_customer_contact_secret: str
    wecom_archive_secret: str
    wecom_archive_sdk_path: str
    wecom_archive_private_key_path: str
    wecom_archive_public_key_version: int
    wecom_archive_seed_seq: int
    wecom_archive_window_step: int
    wecom_archive_page_size: int
    wecom_archive_timeout_seconds: int
    wecom_archive_auto_sync_seconds: float
    wecom_archive_auto_sync_error_seconds: float
    reviewer_userids: frozenset[str]
    ai_model_base_url: str
    ai_model_api_key: str
    ai_model_name: str
    ai_model_timeout_seconds: int
    summary_batch_size: int
    summary_min_new_messages: int
    yuque_mcp_url: str
    yuque_mcp_timeout_seconds: int
    yuque_product_index_repo: str
    yuque_product_index_doc: str
    yuque_product_docs_repo: str
    lightweight_search_enabled: bool
    lightweight_search_url: str
    lightweight_search_timeout_seconds: float
    lightweight_embedding_model: str
    lightweight_embedding_model_path: str
    lightweight_index_path: str
    retrieval_strategy_version: str


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """读取并缓存环境配置。

    Returns:
        当前进程使用的配置快照。
    """

    reviewers = frozenset(
        item.strip()
        for item in os.environ.get("KEFUBAO_REVIEWER_USERIDS", "").split(",")
        if item.strip()
    )
    app_secret = os.environ.get("APP_SECRET", "").strip()
    if os.environ.get("KEFUBAO_ENV", "development").lower() == "production":
        if len(app_secret) < 32 or app_secret.startswith(("replace_", "local-only")):
            raise ValueError("生产环境 APP_SECRET 必须配置为至少 32 字符的随机密钥")
    return Settings(
        app_name="KefuBao WeCom Assistant",
        host=os.environ.get("KEFUBAO_HOST", "127.0.0.1"),
        port=int(os.environ.get("KEFUBAO_AUTH_PORT", "8787")),
        app_secret=app_secret or _LOCAL_APP_SECRET,
        database_url=os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL).strip(),
        wecom_corp_id=os.environ.get("WECOM_CORP_ID", ""),
        wecom_agent_id=os.environ.get("WECOM_AGENT_ID", ""),
        wecom_corp_secret=os.environ.get("WECOM_CORP_SECRET", ""),
        # 通讯录和客户联系 API 可以使用各自后台生成的 Secret。未单独配置时
        # 回退到自建应用 Secret，兼容仅依赖应用可见范围的小规模部署。
        wecom_contacts_secret=os.environ.get("WECOM_CONTACTS_SECRET", "").strip(),
        wecom_customer_contact_secret=os.environ.get(
            "WECOM_CUSTOMER_CONTACT_SECRET", ""
        ).strip(),
        wecom_archive_secret=os.environ.get("WECOM_ARCHIVE_SECRET", "").strip(),
        wecom_archive_sdk_path=os.environ.get("WECOM_ARCHIVE_SDK_PATH", "").strip(),
        wecom_archive_private_key_path=os.environ.get(
            "WECOM_ARCHIVE_PRIVATE_KEY_PATH", ""
        ).strip(),
        wecom_archive_public_key_version=int(
            os.environ.get("WECOM_ARCHIVE_PUBLIC_KEY_VERSION", "0")
        ),
        wecom_archive_seed_seq=int(os.environ.get("WECOM_ARCHIVE_SEED_SEQ", "0")),
        wecom_archive_window_step=int(
            os.environ.get("WECOM_ARCHIVE_WINDOW_STEP", "1000")
        ),
        wecom_archive_page_size=min(
            max(int(os.environ.get("WECOM_ARCHIVE_PAGE_SIZE", "1000")), 1),
            1000,
        ),
        wecom_archive_timeout_seconds=max(
            int(os.environ.get("WECOM_ARCHIVE_TIMEOUT_SECONDS", "30")), 5
        ),
        wecom_archive_auto_sync_seconds=max(
            float(os.environ.get("WECOM_ARCHIVE_AUTO_SYNC_SECONDS", "0.5")), 0.5
        ),
        wecom_archive_auto_sync_error_seconds=max(
            float(os.environ.get("WECOM_ARCHIVE_AUTO_SYNC_ERROR_SECONDS", "10")), 1.0
        ),
        reviewer_userids=reviewers,
        ai_model_base_url=os.environ.get("AI_MODEL_BASE_URL", "").strip(),
        ai_model_api_key=os.environ.get("AI_MODEL_API_KEY", "").strip(),
        ai_model_name=os.environ.get("AI_MODEL_NAME", "MiniMax-M3").strip(),
        ai_model_timeout_seconds=int(os.environ.get("AI_MODEL_TIMEOUT_SECONDS", "30")),
        # 后台摘要只处理新增消息达到阈值的群聊，与高频消息同步进程隔离。
        # 生成回复前的按需摘要不使用该阈值。
        summary_batch_size=min(
            max(int(os.environ.get("KEFUBAO_SUMMARY_BATCH_SIZE", "5")), 1),
            50,
        ),
        summary_min_new_messages=min(
            max(int(os.environ.get("KEFUBAO_SUMMARY_MIN_NEW_MESSAGES", "20")), 1),
            200,
        ),
        yuque_mcp_url=os.environ.get(
            "YUQUE_MCP_URL", "http://127.0.0.1:8003/mcp"
        ).strip(),
        yuque_mcp_timeout_seconds=max(
            int(os.environ.get("YUQUE_MCP_TIMEOUT_SECONDS", "15")), 3
        ),
        yuque_product_index_repo=os.environ.get(
            "YUQUE_PRODUCT_INDEX_REPO", ""
        ).strip(),
        yuque_product_index_doc=os.environ.get(
            "YUQUE_PRODUCT_INDEX_DOC", ""
        ).strip(),
        yuque_product_docs_repo=os.environ.get(
            "YUQUE_PRODUCT_DOCS_REPO", ""
        ).strip(),
        lightweight_search_enabled=os.environ.get(
            "KEFUBAO_LIGHTWEIGHT_SEARCH_ENABLED", "false"
        ).strip().casefold() in {"1", "true", "yes", "on"},
        lightweight_search_url=os.environ.get(
            "KEFUBAO_LIGHTWEIGHT_SEARCH_URL", "http://127.0.0.1:8791"
        ).strip().rstrip("/"),
        lightweight_search_timeout_seconds=max(
            float(os.environ.get("KEFUBAO_LIGHTWEIGHT_SEARCH_TIMEOUT_SECONDS", "0.8")),
            0.1,
        ),
        lightweight_embedding_model=os.environ.get(
            "KEFUBAO_LIGHTWEIGHT_EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5"
        ).strip(),
        lightweight_embedding_model_path=os.environ.get(
            "KEFUBAO_LIGHTWEIGHT_EMBEDDING_MODEL_PATH", ""
        ).strip(),
        lightweight_index_path=os.environ.get(
            "KEFUBAO_LIGHTWEIGHT_INDEX_PATH", "./data/skill-index"
        ).strip(),
        retrieval_strategy_version=os.environ.get(
            "KEFUBAO_RETRIEVAL_STRATEGY_VERSION", "hybrid-small-v1"
        ).strip(),
    )

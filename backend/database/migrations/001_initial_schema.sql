-- 客服宝 MySQL 8 最小生产结构，仅使用企业微信会话内容存档作为消息来源。
SET NAMES utf8mb4;

CREATE TABLE IF NOT EXISTS users (
  wecom_userid VARCHAR(128) PRIMARY KEY,
  username VARCHAR(100) NULL UNIQUE,
  password_hash VARCHAR(255) NULL,
  name VARCHAR(100) NOT NULL,
  role VARCHAR(32) NOT NULL DEFAULT 'staff',
  is_enabled BOOLEAN NOT NULL DEFAULT FALSE,
  must_change_password BOOLEAN NOT NULL DEFAULT FALSE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS customers (
  external_userid VARCHAR(128) PRIMARY KEY,
  name VARCHAR(255) NOT NULL DEFAULT '当前客户'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS conversations (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  wecom_chat_id VARCHAR(255) NOT NULL UNIQUE,
  conversation_type VARCHAR(32) NOT NULL,
  name VARCHAR(255) NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS conversation_users (
  conversation_id BIGINT NOT NULL,
  wecom_userid VARCHAR(128) NOT NULL,
  PRIMARY KEY (conversation_id, wecom_userid),
  FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
  FOREIGN KEY (wecom_userid) REFERENCES users(wecom_userid) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS conversation_customers (
  conversation_id BIGINT NOT NULL,
  external_userid VARCHAR(128) NOT NULL,
  PRIMARY KEY (conversation_id, external_userid),
  FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
  FOREIGN KEY (external_userid) REFERENCES customers(external_userid) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS messages (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  conversation_id BIGINT NOT NULL,
  archive_seq BIGINT NOT NULL UNIQUE,
  sender_wecom_userid VARCHAR(128) NULL,
  sender_external_userid VARCHAR(128) NULL,
  message_type VARCHAR(32) NOT NULL,
  content_json JSON NOT NULL,
  content_text TEXT NULL,
  sent_at DATETIME(3) NOT NULL,
  KEY idx_messages_conversation_time (conversation_id, sent_at, id),
  FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
  FOREIGN KEY (sender_wecom_userid) REFERENCES users(wecom_userid) ON DELETE SET NULL,
  FOREIGN KEY (sender_external_userid) REFERENCES customers(external_userid) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS archive_sync_state (
  id TINYINT PRIMARY KEY,
  lower_seq BIGINT NOT NULL DEFAULT 0,
  upper_seq BIGINT NOT NULL DEFAULT 0,
  reached_end BOOLEAN NOT NULL DEFAULT FALSE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS ai_replies (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  conversation_id BIGINT NOT NULL,
  operator_userid VARCHAR(128) NOT NULL,
  question_summary TEXT NOT NULL,
  context_message_ids JSON NOT NULL,
  generated_reply TEXT NOT NULL,
  final_reply TEXT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'generated',
  generated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  KEY idx_ai_replies_conversation_time (conversation_id, generated_at),
  FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
  FOREIGN KEY (operator_userid) REFERENCES users(wecom_userid) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS conversation_summaries (
  conversation_id BIGINT PRIMARY KEY,
  summary_text TEXT NOT NULL,
  analysis_json JSON NOT NULL,
  last_message_id BIGINT NOT NULL,
  FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
  FOREIGN KEY (last_message_id) REFERENCES messages(id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS skills (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  source_conversation_id BIGINT NULL,
  ai_reply_id BIGINT NULL,
  source_message_ids JSON NOT NULL,
  title VARCHAR(255) NOT NULL,
  content TEXT NOT NULL,
  taxonomy_path JSON NOT NULL,
  quality_score DECIMAL(5,2) NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'pending',
  submitted_by VARCHAR(128) NULL,
  reviewer_userid VARCHAR(128) NULL,
  review_opinion TEXT NULL,
  expires_at DATETIME(3) NULL,
  version BIGINT NOT NULL DEFAULT 1,
  KEY idx_skills_status_score (status, quality_score),
  FULLTEXT KEY ft_skills_title (title) WITH PARSER ngram,
  FULLTEXT KEY ft_skills_content (content) WITH PARSER ngram,
  FOREIGN KEY (source_conversation_id) REFERENCES conversations(id) ON DELETE SET NULL,
  FOREIGN KEY (ai_reply_id) REFERENCES ai_replies(id) ON DELETE SET NULL,
  FOREIGN KEY (submitted_by) REFERENCES users(wecom_userid) ON DELETE SET NULL,
  FOREIGN KEY (reviewer_userid) REFERENCES users(wecom_userid) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS skill_review_events (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  skill_id BIGINT NOT NULL,
  reviewer_userid VARCHAR(128) NOT NULL,
  action VARCHAR(32) NOT NULL,
  reason_codes JSON NOT NULL,
  opinion TEXT NOT NULL,
  before_snapshot JSON NOT NULL,
  after_snapshot JSON NOT NULL,
  governance_snapshot JSON NOT NULL,
  request_id VARCHAR(64) NOT NULL UNIQUE,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  KEY idx_skill_review_events_skill_time (skill_id, created_at, id),
  FOREIGN KEY (skill_id) REFERENCES skills(id) ON DELETE RESTRICT,
  FOREIGN KEY (reviewer_userid) REFERENCES users(wecom_userid) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS ai_reply_events (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  ai_reply_id BIGINT NOT NULL,
  operator_userid VARCHAR(128) NULL,
  event_type VARCHAR(32) NOT NULL,
  payload_json JSON NOT NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  KEY idx_ai_reply_events_reply_time (ai_reply_id, created_at, id),
  FOREIGN KEY (ai_reply_id) REFERENCES ai_replies(id) ON DELETE RESTRICT,
  FOREIGN KEY (operator_userid) REFERENCES users(wecom_userid) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS retrieval_runs (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  ai_reply_id BIGINT NOT NULL,
  trace_id VARCHAR(64) NOT NULL UNIQUE,
  query_text TEXT NOT NULL,
  intent_snapshot JSON NOT NULL,
  scope_json JSON NOT NULL,
  strategy_version VARCHAR(64) NOT NULL,
  embedding_model VARCHAR(128) NOT NULL DEFAULT '',
  index_version VARCHAR(128) NOT NULL DEFAULT '',
  ranking_rule_version VARCHAR(64) NOT NULL DEFAULT '',
  selected_refs JSON NOT NULL,
  timings_json JSON NOT NULL,
  fallback_reason TEXT NOT NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  FOREIGN KEY (ai_reply_id) REFERENCES ai_replies(id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS retrieval_candidates (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  retrieval_run_id BIGINT NOT NULL,
  source_type VARCHAR(32) NOT NULL,
  source_ref VARCHAR(255) NOT NULL,
  stage VARCHAR(32) NOT NULL,
  rank INT NOT NULL,
  score DECIMAL(12,6) NULL,
  reason VARCHAR(255) NOT NULL DEFAULT '',
  detail_json JSON NOT NULL,
  KEY idx_retrieval_candidates_run_stage (retrieval_run_id, stage, rank),
  FOREIGN KEY (retrieval_run_id) REFERENCES retrieval_runs(id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS skill_index_jobs (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  skill_id BIGINT NOT NULL,
  operation VARCHAR(16) NOT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'pending',
  attempts INT NOT NULL DEFAULT 0,
  last_error TEXT NOT NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  processed_at DATETIME(3) NULL,
  KEY idx_skill_index_jobs_status_id (status, id),
  FOREIGN KEY (skill_id) REFERENCES skills(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

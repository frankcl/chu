CREATE TABLE IF NOT EXISTS chat_summary (
    id BIGINT NOT NULL AUTO_INCREMENT,
    session_id VARCHAR(36) NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    summary JSON NOT NULL,
    covered_from_seq INT NOT NULL,
    covered_through_seq INT NOT NULL,
    covered_message_count INT NOT NULL,
    summary_version INT NOT NULL DEFAULT 1,
    estimated_tokens INT NOT NULL DEFAULT 0,
    create_time BIGINT NOT NULL,
    update_time BIGINT NOT NULL,
    PRIMARY KEY (id),
    KEY ix_chat_summary_session_id (session_id),
    KEY ix_chat_summary_user_id (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

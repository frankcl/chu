"""对话历史持久化（MySQL）。存储层，与 agent 运行时解耦。

两个维度：
- ``chat_session``（对话 session）：一场对话，id 即运行时 session_id；历史与
  短期 MemoryManager 通过这个同一 id 关联。
- ``chat_message``（消息，区分类型）：``role`` × ``type``（text/thinking/tool/
  plan/step）全量记录；同一 session 下按 ``seq`` 排序组成完整对话。
- ``chat_summary``：当前有效的滚动 Memory Summary 及其覆盖的正文消息范围；
  ``session_id`` 为普通索引，当前通过事务保证每个对话仅一行。

这里的「对话历史」是全量、持久、可浏览的记录，区别于「对话记忆」
（MemoryManager 生成的有界 LLM 上下文）。未配置 MySQL 或连接失败时，所有 helper 优雅
降级（写入静默、读取返回空），不阻断对话主流程 —— 与 OSS 未配置时同样的思路。

所有表统一 ``create_time`` / ``update_time``，均为毫秒时间戳（BIGINT），与 hylian
models 的时间字段约定对齐。
"""

from storage.chat_message import ChatMessage, append_messages, get_messages
from storage.chat_summary import ChatSummary, load_memory_state, save_chat_summary
from storage.chat_session import (
    ChatSession,
    add_session_usage,
    create_conversation,
    delete_conversation,
    delete_user_history,
    get_owner,
    list_conversations,
    set_top,
    update_title,
    user_token_stats,
)
from storage.db import Base, enabled, init_db

__all__ = [
    "Base",
    "ChatMessage",
    "ChatSession",
    "ChatSummary",
    "add_session_usage",
    "append_messages",
    "create_conversation",
    "delete_conversation",
    "delete_user_history",
    "enabled",
    "get_messages",
    "get_owner",
    "init_db",
    "list_conversations",
    "load_memory_state",
    "save_chat_summary",
    "set_top",
    "update_title",
    "user_token_stats",
]

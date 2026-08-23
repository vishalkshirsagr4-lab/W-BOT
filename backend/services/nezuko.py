import logging
import re
import time
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from backend.ai.chat import normalize_history_for_gemini
from backend.config.settings import get_settings
from backend.database.connection import db_instance

logger = logging.getLogger(__name__)
settings = get_settings()

WAKE_WORD = getattr(settings, "NEZUKO_WAKE_WORD", "nezuko") or "nezuko"


@lru_cache(maxsize=128)
def _cached_setting_value(key: str, value: str) -> str:
    return value


def get_cached_setting(key: str, default: str = "") -> str:
    return _cached_setting_value(key, getattr(settings, key, default) or default)


def sanitize_text(text: str | None) -> str:
    """Normalize inbound text and remove control characters before processing."""
    if not text:
        return ""
    cleaned = re.sub(r"[\x00-\x1f\x7f]", " ", str(text))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def should_trigger_nezuko(text: str | None) -> bool:
    """Return True when the user explicitly addresses Nezuko."""
    normalized = sanitize_text(text).lower()
    if not normalized:
        return False
    return WAKE_WORD in normalized or normalized.startswith("/")


def is_authorized_admin(phone_number: str | None) -> bool:
    """Return True when the supplied phone is an authorized admin."""
    if not phone_number:
        return False

    def normalize(value: str | None) -> str:
        if not value:
            return ""
        cleaned = str(value).strip().lower()
        cleaned = re.sub(r"[^0-9]", "", cleaned)
        return cleaned

    normalized = normalize(phone_number)
    allowed = ",".join(
        value for value in (
            getattr(settings, "ADMIN_PHONE_NUMBERS", ""),
            getattr(settings, "ADMIN_JIDS", ""),
            getattr(settings, "OWNER_NUMBER", ""),
        ) if value
    )

    candidates = [normalized_candidate for candidate in allowed.split(",") if candidate.strip() for normalized_candidate in [normalize(candidate)] if normalized_candidate]

    return normalized in candidates


def build_help_text() -> str:
    """Create the WhatsApp help text for the Nezuko assistant."""
    return (
        "Nezuko commands:\n"
        "• help / menu / about - show this help\n"
        "• ping / status - check the bot health\n"
        "• reset memory / clear chat - clear the conversation memory\n"
        "• ai / chat / explain / summary - talk to the AI\n"
        "• translate / time / date / joke / quote / motivate - quick utilities\n"
        "• admin: broadcast / shutdown / restart / statistics / logs / maintenance mode"
    )


def build_command_help(command: str) -> str:
    """Return help text for a specific command."""
    command_map = {
        "help": "Use help, menu, or about to see the available commands.",
        "about": "Nezuko is your AI WhatsApp assistant with memory, commands, and admin controls.",
        "menu": "Show the main command menu for Nezuko.",
        "ping": "Reply with pong to confirm the bot is online.",
        "status": "Show a quick health snapshot for bot, AI, and database services.",
        "reset memory": "Clear the stored conversation context for this chat.",
        "clear chat": "Clear the current chat memory for this conversation.",
        "ai": "Start a conversational AI reply using Nezuko's memory.",
        "summary": "Summarize the latest conversation context.",
        "translate": "Translate a short phrase to English or ask for a language.",
        "time": "Tell the current time.",
        "date": "Tell the current date.",
        "joke": "Share a playful joke.",
        "quote": "Share a short motivational quote.",
        "motivate": "Send a motivational encouragement.",
        "news": "Fetch the latest news if the feature is enabled.",
        "explain": "Ask Nezuko to explain a topic in simple words.",
        "chat": "Continue the conversation naturally with Nezuko.",
    }
    return command_map.get(command.lower(), f"No help text is available for {command}.")


async def get_conversation_history(db: Any, chat_id: str, phone_number: str) -> list[dict[str, Any]]:
    """Load recent conversation turns from MongoDB for a chat."""
    if db is None:
        return []
    try:
        doc = await db["conversations"].find_one({"chat_id": chat_id})
        if not doc:
            return []
        return list(doc.get("messages", [])[-int(getattr(settings, "CONVERSATION_MAX_MESSAGES", 200)):])
    except Exception:
        logger.exception("Failed to load conversation history chat_id=%s", chat_id)
        return []


async def save_conversation_history(db: Any, chat_id: str, phone_number: str, user_message: str, reply: str) -> None:
    """Persist recent exchanges without allowing a chat document to grow forever."""
    if db is None:
        return
    try:
        now = int(time.time())
        expires_at = datetime.fromtimestamp(
            now + int(getattr(settings, "CONVERSATION_TTL_SECONDS", 60 * 60 * 24 * 7)),
            tz=timezone.utc,
        )
        max_messages = max(2, int(getattr(settings, "CONVERSATION_MAX_MESSAGES", 200)))
        max_chars = max(1, int(getattr(settings, "CONVERSATION_MAX_MESSAGE_CHARS", 12000)))
        await db["conversations"].update_one(
            {"chat_id": chat_id},
            {
                "$set": {
                    "phone_number": phone_number,
                    "updated_at": now,
                    "expires_at": expires_at,
                    "context": "WhatsApp conversation",
                },
                "$push": {
                    "messages": {
                        "$each": [
                            {"role": "user", "text": sanitize_text(user_message)[:max_chars], "timestamp": now},
                            {"role": "assistant", "text": sanitize_text(reply)[:max_chars], "timestamp": now},
                        ],
                        "$slice": -max_messages,
                    }
                },
            },
            upsert=True,
        )
    except Exception:
        logger.exception("Failed to save conversation history chat_id=%s", chat_id)


async def clear_conversation_history(db: Any, chat_id: str) -> bool:
    """Clear the saved memory for a conversation."""
    if db is None:
        return False
    try:
        result = await db["conversations"].delete_one({"chat_id": chat_id})
        return result.deleted_count > 0
    except Exception:
        logger.exception("Failed to clear conversation history chat_id=%s", chat_id)
        return False


async def prune_expired_conversations(db: Any) -> int:
    """Remove stale conversation documents based on their expiry time."""
    if db is None:
        return 0
    try:
        now = datetime.now(timezone.utc)
        result = await db["conversations"].delete_many(
            {
                "$or": [
                    {"expires_at": {"$lt": now}},
                    {"expires_at": {"$lt": int(now.timestamp())}},
                ]
            }
        )
        return int(result.deleted_count or 0)
    except Exception:
        logger.exception("Failed to prune expired conversations")
        return 0

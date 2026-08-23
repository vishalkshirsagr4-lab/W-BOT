import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.ai.chat import generate_chat_response, normalize_history_for_gemini
from backend.services.nezuko import (
    build_command_help,
    build_help_text,
    clear_conversation_history,
    get_conversation_history,
    is_authorized_admin,
    save_conversation_history,
    sanitize_text,
    should_trigger_nezuko,
)


def extract_command(text: str) -> str:
    """Extract the command text after the wake word, if present."""
    cleaned = sanitize_text(text).strip()
    if not cleaned:
        return ""

    lowered = cleaned.lower()
    if lowered.startswith("nezuko"):
        remainder = cleaned[6:].strip()
        return remainder if remainder else ""

    if " nezuko " in lowered:
        parts = cleaned.split()
        try:
            idx = [i for i, token in enumerate(parts) if token.lower() == "nezuko"][0]
        except IndexError:
            return cleaned
        return " ".join(parts[idx + 1 :]).strip()

    return cleaned

logger = logging.getLogger(__name__)


def _collection(db: Any, name: str) -> Any:
    """Support Motor databases and the lightweight database doubles used in tests."""
    try:
        return db[name]
    except (TypeError, KeyError, AttributeError):
        return getattr(db, name)


async def _record_admin_action(db: Any, action: str, payload: dict[str, Any], **details: Any) -> None:
    """Persist an audit record without making an admin command depend on logging."""
    try:
        await _collection(db, "admin_actions").insert_one({
            "action": action,
            "admin_phone": payload.get("phone_number", ""),
            "chat_id": payload.get("chat_id", ""),
            "details": details,
            "created_at": datetime.now(timezone.utc),
        })
    except Exception:
        logger.exception("Could not record admin action=%s", action)


async def _admin_command(db: Any, payload: dict[str, Any], command_text: str) -> str:
    """Execute an admin command received through the Nezuko WhatsApp flow."""
    if command_text.lower().startswith("/admin"):
        command_text = command_text[6:].strip()
    action, _, argument = command_text.partition(" ")
    action = action.lower()
    argument = argument.strip()

    if action == "broadcast":
        if not argument:
            return "Usage: Nezuko broadcast <message>"
        announcement = {
            "message": argument,
            "author_phone": payload.get("phone_number", ""),
            "created_at": datetime.now(timezone.utc),
            "status": "queued",
        }
        await _collection(db, "announcements").insert_one(announcement)
        await _record_admin_action(db, "broadcast", payload, message=argument)
        return "Broadcast queued successfully."

    if action in {"shutdown", "restart"}:
        await _collection(db, "admin_actions").insert_one({
            "action": action,
            "requested_by": payload.get("phone_number", ""),
            "chat_id": payload.get("chat_id", ""),
            "status": "requested",
            "created_at": datetime.now(timezone.utc),
        })
        return f"{action.title()} request recorded. The process supervisor must perform it."

    if action in {"statistics", "stats"}:
        users = await _collection(db, "users").count_documents({})
        notes = await _collection(db, "notes").count_documents({})
        conversations = await _collection(db, "conversations").count_documents({})
        return f"Statistics:\nUsers: {users}\nNotes: {notes}\nConversations: {conversations}"

    if action == "active" and argument == "users":
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=15)
        count = await _collection(db, "users").count_documents({"updated_at": {"$gte": cutoff}})
        return f"Active users in the last 15 minutes: {count}"

    if action == "logs":
        cursor = _collection(db, "admin_actions").find({}).sort("created_at", -1)
        entries = await cursor.to_list(length=5)
        if not entries:
            return "No admin logs found."
        return "Recent admin logs:\n" + "\n".join(
            f"- {entry.get('action', 'unknown')} ({entry.get('status', 'completed')})" for entry in entries
        )

    if action == "database" and argument == "status":
        try:
            await db.client.admin.command("ping")
            return "Database status: MongoDB is connected."
        except AttributeError:
            try:
                await db.command("ping")
                return "Database status: MongoDB is connected."
            except Exception:
                return "Database status: MongoDB handle is available, but ping is unavailable."
        except Exception:
            return "Database status: MongoDB is unreachable."

    if action == "system" and argument == "health":
        return "System health: FastAPI is running; MongoDB and WhatsApp are available to the current process."

    if action == "maintenance" and argument in {"", "mode", "on", "off"}:
        enabled = argument != "off"
        await _collection(db, "settings").update_one(
            {"_id": "maintenance"},
            {"$set": {"enabled": enabled, "updated_at": datetime.now(timezone.utc)}},
            upsert=True,
        )
        await _record_admin_action(db, "maintenance mode", payload, enabled=enabled)
        return f"Maintenance mode {'enabled' if enabled else 'disabled'}."

    return "Admin commands: broadcast <message>, shutdown, restart, statistics, logs, database status, system health, maintenance mode, active users"


async def handle_nezuko_command(db: Any, payload: dict[str, Any], text: str) -> dict[str, Any]:
    """Route a Nezuko-triggered message to the right command or generic AI flow."""
    message_text = sanitize_text(text)
    command_text = extract_command(message_text)
    normalized = command_text.lower().strip()
    if normalized.startswith("/admin "):
        normalized = normalized[7:].strip()

    if not should_trigger_nezuko(message_text):
        return {"status": "ignored", "reply": "", "reason": "no_trigger"}

    admin_actions = {"shutdown", "restart", "status", "statistics", "stats", "logs", "database status", "system health", "maintenance", "maintenance mode", "active users"}
    is_admin_command = normalized == "broadcast" or normalized.startswith("broadcast ") or normalized.startswith("/admin ") or normalized in admin_actions or normalized.startswith("maintenance ")
    if is_admin_command:
        if not (is_authorized_admin(payload.get("phone_number")) or is_authorized_admin(payload.get("platform_id"))):
            return {"status": "success", "reply": "Only an authorized admin can use that command."}
        if normalized == "status":
            return {"status": "success", "reply": "Admin status: FastAPI is running; MongoDB and WhatsApp are available to the current process."}
        return {"status": "success", "reply": await _admin_command(db, payload, command_text)}

    if normalized in {"help", "menu", "about"}:
        return {"status": "success", "reply": build_help_text()}

    if normalized in {"ping", "status"}:
        return {"status": "success", "reply": "Nezuko is online and ready, senpai! 🌸"}

    if normalized in {"reset memory", "clear chat"}:
        cleared = await clear_conversation_history(db, payload.get("chat_id", ""))
        return {"status": "success", "reply": "Conversation memory cleared. Ask me anything again, baka! ✨" if cleared else "No stored memory was found for this chat."}

    if normalized.startswith("help "):
        command = normalized.split(" ", 1)[1].strip()
        return {"status": "success", "reply": build_command_help(command)}

    if normalized in {"summary", "summarize"}:
        history = await get_conversation_history(db, payload.get("chat_id", ""), payload.get("phone_number", ""))
        if not history:
            return {"status": "success", "reply": "There is no saved conversation to summarize yet."}
        summary_prompt = "Summarize the following conversation in a concise and friendly way: " + str(history[-6:])
        reply = await generate_chat_response(summary_prompt, chat_history=[])
        return {"status": "success", "reply": reply}

    if normalized in {"translate", "translation"}:
        return {"status": "success", "reply": "Send a phrase and I can translate it for you, senpai. 🌸"}

    if normalized in {"time"}:
        current_time = datetime.now().strftime("%I:%M %p")
        return {"status": "success", "reply": f"The current time is {current_time}."}

    if normalized in {"date"}:
        current_date = datetime.now().strftime("%A, %B %d, %Y")
        return {"status": "success", "reply": f"Today is {current_date}."}

    if normalized in {"joke"}:
        return {"status": "success", "reply": "Why did the developer bring a ladder to the codebase? Because the app needed a higher level! 😄"}

    if normalized in {"quote"}:
        return {"status": "success", "reply": "“Small steps every day still move you forward.” ✨"}

    if normalized in {"motivate"}:
        return {"status": "success", "reply": "You are doing better than you think, senpai. Keep going! 🌈"}

    if normalized.startswith("explain "):
        topic = message_text[len("explain "):].strip()
        reply = await generate_chat_response(f"Explain the topic '{topic}' in simple words and keep the answer friendly.", chat_history=[])
        return {"status": "success", "reply": reply}

    history = await get_conversation_history(db, payload.get("chat_id", ""), payload.get("phone_number", ""))
    history_payload = [{"role": item.get("role", "user"), "parts": [item.get("text", "")]} for item in history]
    history_payload = normalize_history_for_gemini(history_payload)
    reply = await generate_chat_response(message_text, history_payload)
    await save_conversation_history(db, payload.get("chat_id", ""), payload.get("phone_number", ""), message_text, reply)
    return {"status": "success", "reply": reply}

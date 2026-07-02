import logging
import time
from typing import Any, Dict, Optional, Tuple

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ai.chat import generate_chat_response
from database.connection import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/whatsapp", tags=["WhatsApp Integration"])

MAX_MESSAGE_LENGTH_FALLBACK = 4000


class WhatsAppMessagePayload(BaseModel):
    platform_id: str = Field(..., description="Sender platform id (phone@c.us or author@...) / user id")
    phone_number: str = Field(default="", description="Phone number without @c.us")

    sender_name: str = Field(default="")
    profile_name: str = Field(default="")

    chat_id: str = Field(..., description="Chat id")
    group_id: Optional[str] = Field(default=None)
    group_name: Optional[str] = Field(default=None)

    message: str = Field(..., description="Message text")
    quoted_message: Optional[str] = Field(default=None)

    media: Optional[Dict[str, Any]] = Field(default=None)
    location: Optional[Dict[str, Any]] = Field(default=None)
    sticker: Optional[str] = Field(default=None)
    voice: Optional[str] = Field(default=None)

    timestamp: int = Field(...)
    message_type: str = Field(default="text")

    is_group: bool = Field(default=False)
    quoted_text: Optional[str] = Field(default=None)


def _message_text(payload: WhatsAppMessagePayload) -> str:
    return str(payload.message or "").strip()


def _is_command(text: str) -> bool:
    return text.startswith("/")


async def _handle_slash_command(request: Request, db, payload: WhatsAppMessagePayload, text: str) -> Optional[dict]:
    command = text.strip()
    lowered = command.lower()

    if lowered in {"/help", "help"}:
        return {
            "status": "success",
            "reply": (
                "Available WhatsApp commands:\n"
                "• /help - show this help\n"
                "• /game meme or /game joke\n"
                "• /study <subject> [type]\n"
                "• /user - your profile\n"
                "• /utils weather <city>\n"
                "• /utils calc <expression>\n"
                "• /utils qr <text>\n"
                "• /utils convert <value> <from> <to>\n"
                "• /admin stats (admin only)"
            ),
        }

    if lowered.startswith("/game "):
        client = getattr(request.app.state, "http_client", None)
        if client is None:
            return {"status": "error", "reply": "HTTP client unavailable"}

        mode = command.split(maxsplit=2)[1].strip().lower()
        if mode == "meme":
            try:
                response = await client.get("https://meme-api.com/gimme/wholesomememes", timeout=3.0)
                response.raise_for_status()
                data = response.json()
                return {"status": "success", "reply": f"Meme: {data.get('title', 'Here you go')}\n{data.get('url', '')}"}
            except httpx.HTTPError:
                return {"status": "error", "reply": "Meme service is unavailable right now."}

        if mode == "joke":
            try:
                response = await client.get(
                    "https://v2.jokeapi.dev/joke/Programming,Miscellaneous,Pun?safe-mode&type=single",
                    timeout=3.0,
                )
                response.raise_for_status()
                data = response.json()
                return {"status": "success", "reply": data.get("joke", "No joke available right now.")}
            except httpx.HTTPError:
                return {"status": "error", "reply": "Joke service is unavailable right now."}

        return {"status": "error", "reply": "Use /game meme or /game joke"}

    if lowered.startswith("/study "):
        args = command.split()[1:]
        if not args:
            return {"status": "error", "reply": "Use /study <subject> [type]"}
        subject = args[0]
        note_type = args[1] if len(args) > 1 else None
        query = {"subject": {"$regex": f"^{subject}$", "$options": "i"}}
        if note_type:
            query["type"] = note_type
        cursor = db["notes"].find(query).sort("upvotes", -1)
        notes = await cursor.to_list(length=3)
        if not notes:
            return {"status": "error", "reply": f"No study notes found for {subject}."}
        first_note = notes[0]
        return {
            "status": "success",
            "reply": f"Study note found for {subject}: {first_note.get('title', 'Untitled')}\n{first_note.get('content', '')[:600]}",
        }

    if lowered == "/user":
        user = await db["users"].find_one({"phone": payload.phone_number})
        if not user:
            return {"status": "error", "reply": "No profile found yet."}
        return {
            "status": "success",
            "reply": (
                f"Profile:\n"
                f"Name: {user.get('sender_name') or user.get('username') or 'Unknown'}\n"
                f"Phone: {user.get('phone') or payload.phone_number}\n"
                f"Last seen: {user.get('last_seen') or 'n/a'}"
            ),
        }

    if lowered.startswith("/utils "):
        client = getattr(request.app.state, "http_client", None)
        if client is None:
            return {"status": "error", "reply": "HTTP client unavailable"}

        parts = command.split(maxsplit=4)
        if len(parts) < 2:
            return {"status": "error", "reply": "Use /utils weather <city>, /utils calc <expr>, /utils qr <text>, or /utils convert <value> <from> <to>"}

        action = parts[1].lower()
        if action == "weather" and len(parts) >= 3:
            city = parts[2]
            try:
                response = await client.get(
                    f"https://api.openweathermap.org/data/2.5/weather?q={city}&appid={request.app.state.weather_api_key}&units=metric",
                    timeout=3.0,
                )
                response.raise_for_status()
                data = response.json()
                temp = data["main"]["temp"]
                desc = data["weather"][0]["description"].title()
                return {"status": "success", "reply": f"{city}: {temp}°C, {desc}"}
            except Exception:
                return {"status": "error", "reply": "Weather lookup failed."}

        if action == "calc" and len(parts) >= 3:
            expr = parts[2]
            try:
                response = await client.get(f"http://api.mathjs.org/v4/?expr={expr}", timeout=3.0)
                response.raise_for_status()
                return {"status": "success", "reply": f"Result: {response.text}"}
            except Exception:
                return {"status": "error", "reply": "Calculator request failed."}

        if action == "qr" and len(parts) >= 3:
            payload_text = parts[2]
            return {"status": "success", "reply": f"QR code: https://api.qrserver.com/v1/create-qr-code/?size=300x300&data={payload_text}"}

        if action == "convert" and len(parts) >= 5:
            try:
                value = float(parts[2])
                from_unit = parts[3].lower()
                to_unit = parts[4].lower()
            except ValueError:
                return {"status": "error", "reply": "Use /utils convert <value> <from> <to>"}
            if from_unit == "c" and to_unit == "f":
                result = (value * 9 / 5) + 32
            elif from_unit == "f" and to_unit == "c":
                result = (value - 32) * 5 / 9
            elif from_unit == "km" and to_unit == "mi":
                result = value * 0.621371
            elif from_unit == "mi" and to_unit == "km":
                result = value * 1.60934
            elif from_unit == "kg" and to_unit == "lbs":
                result = value * 2.20462
            elif from_unit == "lbs" and to_unit == "kg":
                result = value / 2.20462
            else:
                return {"status": "error", "reply": "Unsupported conversion."}
            return {"status": "success", "reply": f"{value}{from_unit} = {round(result, 2)}{to_unit}"}

        return {"status": "error", "reply": "Unknown utils command."}

    if lowered.startswith("/admin "):
        actor = await db["users"].find_one({"phone": payload.phone_number})
        if not actor or not actor.get("is_admin", False):
            return {"status": "error", "reply": "Only admins can use admin commands."}

        action = command.split(maxsplit=2)[1].strip().lower()
        if action == "stats":
            user_count = await db["users"].count_documents({})
            note_count = await db["notes"].count_documents({})
            confession_count = await db["confessions"].count_documents({})
            poll_count = await db["polls"].count_documents({})
            return {
                "status": "success",
                "reply": f"Admin stats:\nUsers: {user_count}\nNotes: {note_count}\nConfessions: {confession_count}\nPolls: {poll_count}",
            }

        return {"status": "error", "reply": "Admin commands available: /admin stats"}

    return None


def _is_status_or_broadcast(payload: WhatsAppMessagePayload) -> Tuple[bool, Optional[str]]:
    pid = payload.platform_id or ""
    if pid.endswith("@broadcast"):
        return True, "broadcast"
    if pid.endswith("@status"):
        return True, "status"
    return False, None


async def _fetch_one(db, collection: str, query: dict, projection: Optional[dict] = None) -> Optional[dict]:
    try:
        return await db[collection].find_one(query, projection=projection)
    except Exception:
        logger.exception("Mongo read failed collection=%s query=%s", collection, query)
        return None


async def _ensure_user(db, payload: WhatsAppMessagePayload, now_ts: int, text: str) -> None:
    user_update = {
        "$set": {
            "platform_id": payload.platform_id,
            "sender_name": payload.sender_name,
            "profile_name": payload.profile_name,
            "chat_id": payload.chat_id,
            "last_seen": now_ts,
            "last_message": text,
            "role": "User",
            "ai_enabled": True,
            "blocked": False,
            "tags": [],
            "notes": None,
            "updated_at": now_ts,
            "phone": payload.phone_number,
        },
        "$setOnInsert": {
            "created_at": now_ts,
            "first_seen": now_ts,
        },
        "$inc": {"message_count": 1},
    }

    await db["users"].update_one({"phone": payload.phone_number}, user_update, upsert=True)


async def _ensure_group(db, payload: WhatsAppMessagePayload, now_ts: int) -> None:
    if not payload.is_group or not payload.group_id:
        return

    group_update = {
        "$set": {
            "group_id": payload.group_id,
            "group_name": payload.group_name,
            "updated_at": now_ts,
            "last_activity": now_ts,
            "ai_enabled": True,
            "reply_mode": "Always",
        },
        "$setOnInsert": {
            "created_at": now_ts,
            "first_seen": now_ts,
        },
    }

    await db["groups"].update_one({"group_id": payload.group_id}, group_update, upsert=True)


async def _check_blocked(db, payload: WhatsAppMessagePayload, is_group: bool) -> Optional[str]:
    if is_group:
        blocked = await _fetch_one(db, "blocked_groups", {"group_id": payload.group_id}, {"_id": 0, "group_id": 1})
        if blocked:
            return "blocked group"
    else:
        blocked = await _fetch_one(db, "blocked_users", {"phone": payload.phone_number}, {"_id": 0, "phone": 1})
        if blocked:
            return "blocked user"
    return None


async def decide(db, payload: WhatsAppMessagePayload) -> Dict[str, Any]:
    """Return {allowed, ai_enabled, reason, trigger_detected, reply_mode}."""
    is_group = payload.is_group and bool(payload.group_id)

    blocked_reason = await _check_blocked(db, payload, is_group)
    if blocked_reason:
        return {
            "allowed": False,
            "ai_enabled": False,
            "reason": blocked_reason,
            "trigger_detected": True,
            "reply_mode": None,
        }

    if is_group:
        group_doc = await _fetch_one(
            db,
            "groups",
            {"group_id": payload.group_id},
            {"_id": 0, "reply_mode": 1, "ai_enabled": 1},
        )
        reply_mode = str(group_doc.get("reply_mode", "Always")) if group_doc else "Always"
        ai_enabled = bool(group_doc.get("ai_enabled", True)) if group_doc else True
    else:
        chat_setting = await _fetch_one(
            db,
            "chat_settings",
            {"chat_id": payload.chat_id},
            {"_id": 0, "reply_mode": 1, "ai_on": 1},
        )
        reply_mode = str(chat_setting.get("reply_mode", "Always")) if chat_setting else "Always"
        ai_enabled = bool(chat_setting.get("ai_on", True)) if chat_setting else True

    if not ai_enabled:
        return {
            "allowed": False,
            "ai_enabled": False,
            "reason": "AI disabled",
            "trigger_detected": True,
            "reply_mode": reply_mode,
        }

    return {
        "allowed": True,
        "ai_enabled": True,
        "reason": "trigger",
        "trigger_detected": True,
        "reply_mode": reply_mode,
    }


@router.post("/message")
async def receive_whatsapp_message(request: Request, payload: WhatsAppMessagePayload, db=Depends(get_db)):
    started_at = time.perf_counter()
    logger.info("[WA][REQ] received chat_id=%s platform_id=%s", payload.chat_id, payload.platform_id)

    try:
        step_started = time.perf_counter()
        text = _message_text(payload)
        logger.info("[WA][TIMING] validation_ms=%d", int((time.perf_counter() - step_started) * 1000))

        if payload.message and len(payload.message) > MAX_MESSAGE_LENGTH_FALLBACK:
            payload.message = payload.message[:MAX_MESSAGE_LENGTH_FALLBACK]
            text = _message_text(payload)

        is_sb, sb_reason = _is_status_or_broadcast(payload)
        if is_sb:
            logger.info("[WA][TIMING] ignored_status_ms=%d", int((time.perf_counter() - started_at) * 1000))
            return {"status": "ignored", "reason": sb_reason}

        lower = text.lower()
        trigger_word = "nezuko" in lower
        direct_reply = bool(payload.quoted_text) and ("nezuko" in str(payload.quoted_text).lower())
        command_trigger = _is_command(text)
        trigger_detected = trigger_word or direct_reply or command_trigger
        if not trigger_detected:
            logger.info("[WA][TIMING] ignored_trigger_ms=%d", int((time.perf_counter() - started_at) * 1000))
            return {"status": "ignored", "reason": "no trigger word"}

        now_ts = payload.timestamp or int(time.time())
        is_group = payload.is_group and bool(payload.group_id)

        step_started = time.perf_counter()
        await _ensure_user(db, payload, now_ts, text)
        logger.info("[WA][TIMING] mongo_user_upsert_ms=%d", int((time.perf_counter() - step_started) * 1000))

        if is_group:
            step_started = time.perf_counter()
            await _ensure_group(db, payload, now_ts)
            logger.info("[WA][TIMING] mongo_group_upsert_ms=%d", int((time.perf_counter() - step_started) * 1000))

        step_started = time.perf_counter()
        decision = await decide(db, payload)
        logger.info("[WA][TIMING] mongo_decision_ms=%d", int((time.perf_counter() - step_started) * 1000))

        if not decision.get("allowed"):
            logger.info("[WA][TIMING] decision_blocked_ms=%d", int((time.perf_counter() - started_at) * 1000))
            return {"status": "ignored", "reason": str(decision.get("reason") or "unknown")}

        if _is_command(text):
            command_result = await _handle_slash_command(request, db, payload, text)
            if command_result is not None:
                logger.info("[WA][TIMING] command_ms=%d", int((time.perf_counter() - started_at) * 1000))
                return {"status": "success", "reply": command_result["reply"]}

        step_started = time.perf_counter()
        reply = await generate_chat_response(text, [])
        reply = (str(reply) if reply is not None else "").strip()[:4000]
        logger.info("[WA][TIMING] gemini_ms=%d", int((time.perf_counter() - step_started) * 1000))
        logger.info("[WA][TIMING] total_ms=%d", int((time.perf_counter() - started_at) * 1000))

        return {"status": "success", "reply": reply}

    except HTTPException:
        raise
    except Exception:
        logger.exception("WhatsApp message handling failed after_ms=%d", int((time.perf_counter() - started_at) * 1000))
        raise HTTPException(status_code=500, detail="WhatsApp integration error")


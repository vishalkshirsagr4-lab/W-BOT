import logging
import time
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
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
async def receive_whatsapp_message(payload: WhatsAppMessagePayload, db=Depends(get_db)):
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
        trigger_detected = trigger_word or direct_reply
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


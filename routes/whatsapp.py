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
    # Node already best-effort filters, but keep server-side safety.
    pid = payload.platform_id or ""
    if pid.endswith("@broadcast"):
        return True, "broadcast"
    if pid.endswith("@status"):
        return True, "status"
    return False, None


async def _fetch_one(db, collection: str, query: dict) -> Optional[dict]:
    try:
        return await db[collection].find_one(query)
    except Exception:
        logger.exception("Mongo read failed")
        return None


async def _ensure_user(db, payload: WhatsAppMessagePayload, now_ts: int, text: str) -> None:
    # Root requirement: auto upsert; never require manual insertion.
    # Also: avoid duplicate paths in $set/$setOnInsert.
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
            "message_count": 0,
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
            "first_seen": {"$exists": False},
            "last_activity": now_ts,
            "ai_enabled": True,
            "reply_mode": "Always",
        },
        "$setOnInsert": {
            "created_at": now_ts,
            "first_seen": now_ts,
        },
    }

    # Fix: remove the problematic first_seen path from $set above; keep it only in $setOnInsert.
    group_update["$set"].pop("first_seen", None)

    await db["groups"].update_one({"group_id": payload.group_id}, group_update, upsert=True)


async def _check_blocked(db, payload: WhatsAppMessagePayload, is_group: bool) -> Optional[str]:
    # blocked users/groups
    if is_group:
        blocked = await _fetch_one(db, "blocked_groups", {"group_id": payload.group_id})
        if blocked:
            return "blocked group"
    else:
        blocked = await _fetch_one(db, "blocked_users", {"phone": payload.phone_number})
        if blocked:
            return "blocked user"
    return None


async def decide(db, payload: WhatsAppMessagePayload) -> Dict[str, Any]:
    """Return {allowed, ai_enabled, reason, trigger_detected, reply_mode}."""

    text = _message_text(payload)

    # Status/broadcast
    is_sb, sb_reason = _is_status_or_broadcast(payload)
    if is_sb:
        return {
            "allowed": False,
            "ai_enabled": False,
            "reason": sb_reason,
            "trigger_detected": False,
            "reply_mode": None,
        }

    is_group = payload.is_group and bool(payload.group_id)

    # Blocked
    blocked_reason = await _check_blocked(db, payload, is_group)
    if blocked_reason:
        return {
            "allowed": False,
            "ai_enabled": False,
            "reason": blocked_reason,
            "trigger_detected": False,
            "reply_mode": None,
        }

    # Trigger detection
    lower = text.lower()
    trigger_word = "nezuko" in lower
    direct_reply = bool(payload.quoted_text) and ("nezuko" in str(payload.quoted_text).lower())

    trigger_detected = trigger_word or direct_reply

    # Ignore self message: best-effort using platform_id vs sender? The architecture has no self id.
    # Keep it strictly safe: if sender_name equals bot name is unavailable; so we don't apply this here.

    if not trigger_detected:
        # reply only when trigger conditions are met
        return {
            "allowed": False,
            "ai_enabled": False,
            "reason": "no trigger word",
            "trigger_detected": False,
            "reply_mode": None,
        }

    # Chat settings to decide AI enabled
    now = int(time.time())
    if is_group:
        group_doc = await _fetch_one(db, "groups", {"group_id": payload.group_id})
        reply_mode = str(group_doc.get("reply_mode", "Always")) if group_doc else "Always"
        ai_enabled = bool(group_doc.get("ai_enabled", True)) if group_doc else True
    else:
        chat_setting = await _fetch_one(db, "chat_settings", {"chat_id": payload.chat_id})
        reply_mode = str(chat_setting.get("reply_mode", "Always")) if chat_setting else "Always"
        ai_enabled = bool(chat_setting.get("ai_on", True)) if chat_setting else True

    # Disabled chats
    if not ai_enabled:
        return {
            "allowed": False,
            "ai_enabled": False,
            "reason": "AI disabled",
            "trigger_detected": trigger_detected,
            "reply_mode": reply_mode,
        }

    # At this point trigger is detected; return allowed
    return {
        "allowed": True,
        "ai_enabled": True,
        "reason": "trigger",
        "trigger_detected": True,
        "reply_mode": reply_mode,
    }


@router.post("/message")
async def receive_whatsapp_message(payload: WhatsAppMessagePayload, db=Depends(get_db)):
    start = time.perf_counter()
    try:
        # Safety slice
        text = _message_text(payload)
        if payload.message and len(payload.message) > MAX_MESSAGE_LENGTH_FALLBACK:
            payload.message = payload.message[:MAX_MESSAGE_LENGTH_FALLBACK]
            text = _message_text(payload)

        now_ts = payload.timestamp or int(time.time())
        is_group = payload.is_group and bool(payload.group_id)

        # Always auto-register sender + group before any decision
        await _ensure_user(db, payload, now_ts, text)
        if is_group:
            await _ensure_group(db, payload, now_ts)

        decision = await decide(db, payload)

        elapsed_ms = int((time.perf_counter() - start) * 1000)

        logger.info(
            "[WA] Incoming message decision sender=%s chat_id=%s group=%s trigger=%s ai_enabled=%s reply_mode=%s decision=%s reason=%s model=%s elapsed_ms=%s",
            payload.sender_name,
            payload.chat_id,
            payload.group_id if is_group else None,
            decision.get("trigger_detected"),
            decision.get("ai_enabled"),
            decision.get("reply_mode"),
            decision.get("allowed"),
            decision.get("reason"),
            "gemini",
            elapsed_ms,
        )

        if not decision.get("allowed"):
            return {
                "status": "ignored",
                "reason": str(decision.get("reason") or "unknown"),
            }

        if not decision.get("ai_enabled"):
            return {
                "status": "ignored",
                "reason": "AI disabled",
            }

        reply = await generate_chat_response(payload.message, [])
        reply = (str(reply) if reply is not None else "").strip()[:4000]

        return {"status": "success", "reply": reply}

    except HTTPException:
        raise
    except Exception:
        logger.exception("WhatsApp message handling failed")
        raise HTTPException(status_code=500, detail="WhatsApp integration error")


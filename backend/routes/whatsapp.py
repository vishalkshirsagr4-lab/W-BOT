import logging
import time
from typing import Any, Dict, Optional, Tuple

import asyncio

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from backend.ai.chat import generate_chat_response
from backend.database.connection import get_db
from backend.services.commands import handle_nezuko_command
from backend.services.cricket_service import handle_cricket_request, is_cricket_request
from backend.services.nezuko import is_authorized_admin, should_trigger_nezuko, sanitize_text
from backend.services import download_manager
from backend.services import tts_service
from fastapi.responses import FileResponse
import httpx
import os
import shutil

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/whatsapp", tags=["WhatsApp Integration"])

MAX_MESSAGE_LENGTH_FALLBACK = 4000

TTS_USAGE = "🎙️ Usage:\n\n/tts <language> <text>\n\nExample:\n/tts kn ನಮಸ್ಕಾರ!\n/tts hi नमस्ते!\n/tts en Hello!"
TTS_LANGUAGES = "\n".join(f"{code} — {name}" for code, name in tts_service.LANGUAGES.items())
TTS_HELP = (
    "🎙️ Nezuko TTS\n\nConvert text into voice/audio.\n\nUsage:\n\n"
    "/tts <language> <text>\n\nLanguages:\n\n"
    f"{TTS_LANGUAGES}\n\nExamples:\n\n"
    "/tts kn ನಮಸ್ಕಾರ! ಹೇಗಿದ್ದೀರಾ?\n\n/tts hi नमस्ते! कैसे हो?\n\n"
    "/tts en Hello! How are you?\n\n🎧 Send it and I'll turn it into audio!"
)


def _normalize_phone_number(value: Optional[str]) -> str:
    if not value:
        return ""
    return "".join(ch for ch in str(value) if ch.isdigit())


def _collection(db, name: str):
    if db is None:
        raise ValueError("Database is not available")
    try:
        return db[name]
    except (TypeError, KeyError, AttributeError):
        try:
            return getattr(db, name)
        except AttributeError:
            class _FallbackCollection:
                async def find_one(self, *args, **kwargs):
                    return None

                async def count_documents(self, *args, **kwargs):
                    return 0

            return _FallbackCollection()


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


async def _register_or_login_user(db, payload: WhatsAppMessagePayload, name_hint: Optional[str] = None) -> dict:
    normalized_phone = _normalize_phone_number(payload.phone_number)
    username = (name_hint or payload.sender_name or payload.profile_name or "User").strip() or "User"
    query = {"platform_id": payload.platform_id}
    if normalized_phone:
        query = {"$or": [{"platform_id": payload.platform_id}, {"phone": normalized_phone}, {"phone": payload.phone_number}]}

    user_doc = {
        "platform_id": payload.platform_id,
        "phone": normalized_phone or payload.phone_number or payload.platform_id,
        "sender_name": payload.sender_name or username,
        "profile_name": payload.profile_name or username,
        "username": username,
        "first_name": username,
        "language": "en",
        "chat_id": payload.chat_id,
        "last_seen": int(time.time()),
        "updated_at": int(time.time()),
        "role": "User",
        "ai_enabled": True,
        "blocked": False,
        "tags": [],
        "notes": None,
        "coins": 100,
        "xp": 0,
        "level": 1,
        "badges": ["Newbie ✨"],
        "join_date": time.time(),
        "is_admin": bool(is_authorized_admin(payload.phone_number) or is_authorized_admin(payload.platform_id)),
        "is_banned": False,
    }

    users = _collection(db, "users")
    existing = await users.find_one(query)
    if existing:
        await users.update_one({"_id": existing["_id"]}, {"$set": user_doc})
        return {"status": "success", "reply": f"Welcome back, {username}! Your WhatsApp profile is synced and ready. ✨"}

    await users.insert_one(user_doc)
    return {"status": "success", "reply": f"Registration complete, {username}! You are now stored in the bot and ready to use commands. 🌸"}


def _is_command(text: str) -> bool:
    return text.startswith("/")


async def _notify_tts_failure(http_client, chat_id: str, text: str) -> None:
    bridge_url = os.environ.get("WHATSAPP_BRIDGE_INTERNAL_URL", "http://localhost:10000/internal/send_media")
    payload = {"to": chat_id, "text": text}
    try:
        if http_client is None:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(bridge_url, json=payload, timeout=10.0)
        else:
            await http_client.post(bridge_url, json=payload, timeout=10.0)
    except Exception:
        logger.exception("[TTS] failure_notification_failed to=%s", chat_id)


async def _handle_slash_command(request: Request, db, payload: WhatsAppMessagePayload, text: str) -> Optional[dict]:
    command = text.strip()
    lowered = command.lower()

    if is_cricket_request(command):
        return {"status": "success", "reply": await handle_cricket_request(command, db, payload.model_dump())}

    tts_alias = next((alias for alias in ("/tts", "/vc", "/voice") if lowered == alias or lowered.startswith(f"{alias} ")), None)
    if tts_alias:
        parts = command.split(maxsplit=2)
        if len(parts) == 1:
            return {"status": "error", "reply": TTS_USAGE}
        language = parts[1].lower()
        if language == "help" and len(parts) == 2:
            return {"status": "success", "reply": TTS_HELP}
        if language not in tts_service.LANGUAGES:
            return {"status": "error", "reply": f"❌ Unsupported language: {language}\n\nAvailable languages:\n\n{TTS_LANGUAGES}\n\nExample:\n\n/tts kn नमस्कार!"}
        if len(parts) < 3 or not parts[2].strip():
            return {"status": "error", "reply": "❌ Baka! Give me some text to convert into voice 😭"}
        voice_text = parts[2].strip()
        if len(voice_text) > tts_service.MAX_TTS_CHARS:
            return {"status": "error", "reply": "❌ That's too much text for one voice message 😭\n\nPlease keep it under 3000 characters."}

        try:
            http_client = getattr(request.app.state, "http_client", None)
        except Exception:
            http_client = None

        async def _background_tts():
            audio_path = None
            logger.info("[TTS] command_detected language=%s text_length=%d", language, len(voice_text))
            try:
                audio_path = await tts_service.text_to_speech(voice_text, language)
                filename = os.path.basename(audio_path)
                fastapi_base = os.environ.get("FASTAPI_URL", f"http://localhost:{request.url.port or 8000}")
                file_url = f"{fastapi_base.rstrip('/')}/api/v1/whatsapp/tts/{filename}"
                bridge_url = os.environ.get("WHATSAPP_BRIDGE_INTERNAL_URL", "http://localhost:10000/internal/send_media")
                send_payload = {"to": payload.chat_id, "media_url": file_url, "media_type": "audio", "filename": filename, "caption": "🎧 Done!"}
                logger.info("[TTS] whatsapp_upload_started to=%s", payload.chat_id)
                if http_client is None:
                    async with httpx.AsyncClient(timeout=60.0) as client:
                        response = await client.post(bridge_url, json=send_payload, timeout=60.0)
                else:
                    response = await http_client.post(bridge_url, json=send_payload, timeout=60.0)
                response.raise_for_status()
                logger.info("[TTS] whatsapp_upload_completed to=%s", payload.chat_id)
            except tts_service.TTSBusyError:
                logger.warning("[TTS] busy to=%s", payload.chat_id)
                await _notify_tts_failure(http_client, payload.chat_id, "⏳ Too many voice requests right now, Senpai!\n\nTry again in a moment 🎙️")
            except Exception:
                logger.exception("[TTS] request_failed to=%s", payload.chat_id)
                await _notify_tts_failure(http_client, payload.chat_id, "❌ I couldn't create the voice right now, Senpai 😭\n\nPlease try again.")
            finally:
                if audio_path:
                    tts_service.remove_audio(audio_path)

        asyncio.create_task(_background_tts())
        return {"status": "success", "reply": "⏳ Creating your voice... 🎙️✨"}

    # /dw <url> - download media via yt-dlp and send back to WhatsApp
    if lowered.startswith("/dw"):
        parts = command.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            return {"status": "error", "reply": "❌ Baka! Send a video URL after /dw 😭"}

        url = parts[1].strip()
        # basic URL validation
        if not (url.startswith("http://") or url.startswith("https://")):
            return {"status": "error", "reply": "❌ That doesn't look like a valid video URL 😭"}

        # Acknowledge immediately and start background download
        try:
            http_client: httpx.AsyncClient = getattr(request.app.state, "http_client", None)
        except Exception:
            http_client = None

        # spawn background task so we don't block the FastAPI request
        async def _background():
            logger.info("[DW] download_started url=%s chat_id=%s", url, payload.chat_id)
            try:
                download_id, filename = await download_manager.download_video(url)
                # build public URL for the bridge to fetch
                fastapi_base = os.environ.get("FASTAPI_URL", f"http://localhost:{request.url.port or 8000}")
                file_url = f"{fastapi_base.rstrip('/')}/api/v1/whatsapp/downloads/{download_id}/{filename}"
                logger.info("[DW] download_completed file=%s size=%s", filename, os.path.getsize(os.path.join(download_manager.DOWNLOADS_ROOT, download_id, filename)))

                # notify whatsapp bridge to send the media
                bridge_url = os.environ.get("WHATSAPP_BRIDGE_INTERNAL_URL", "http://localhost:10000/internal/send_media")
                payload_json = {
                    "to": payload.chat_id,
                    "media_url": file_url,
                    "media_type": "video",
                    "filename": filename,
                    "caption": "✅ Done! 🎬✨",
                }
                try:
                    if http_client is None:
                        async with httpx.AsyncClient(timeout=30.0) as client:
                            await client.post(bridge_url, json=payload_json, timeout=30.0)
                    else:
                        await http_client.post(bridge_url, json=payload_json, timeout=30.0)
                    logger.info("[DW] whatsapp_upload_requested to=%s file=%s", payload.chat_id, filename)
                except Exception:
                    logger.exception("[DW] whatsapp_upload_request_failed to=%s", payload.chat_id)
            except download_manager.DownloadError as exc:
                logger.exception("[DW] download_failed url=%s err=%s", url, exc)
                # try to notify user of failure via bridge
                try:
                    bridge_url = os.environ.get("WHATSAPP_BRIDGE_INTERNAL_URL", "http://localhost:10000/internal/send_media")
                    fail_payload = {"to": payload.chat_id, "text": "❌ I couldn't download that video. Try another link!"}
                    if http_client is None:
                        async with httpx.AsyncClient(timeout=10.0) as client:
                            await client.post(bridge_url, json=fail_payload, timeout=10.0)
                    else:
                        await http_client.post(bridge_url, json=fail_payload, timeout=10.0)
                except Exception:
                    logger.exception("[DW] failed to notify user of download failure to=%s", payload.chat_id)
            except Exception as exc:
                logger.exception("[DW] unexpected_error url=%s err=%s", url, exc)

        asyncio.create_task(_background())
        return {"status": "success", "reply": "⏳ Nezuko is downloading your video... 🎬"}

    if lowered in {"/help", "help"}:
        return {
            "status": "success",
            "reply": (
                "Available WhatsApp commands:\n"
                "• /register [name] - save your profile\n"
                "• /login [name] - sync your profile\n"
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

    if lowered in {"/register", "/login"} or lowered.startswith("/register ") or lowered.startswith("/login "):
        name_hint = None
        if len(command.split()) > 1:
            name_hint = " ".join(command.split()[1:]).strip()
        return await _register_or_login_user(db, payload, name_hint)

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
        users = _collection(db, "users")
        user = await users.find_one({"platform_id": payload.platform_id})
        if not user and payload.phone_number:
            user = await users.find_one({"phone": payload.phone_number})
        if not user and payload.chat_id:
            user = await users.find_one({"chat_id": payload.chat_id})
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
        if not is_authorized_admin(payload.phone_number) and not is_authorized_admin(payload.platform_id):
            return {"status": "error", "reply": "Only admins can use admin commands."}

        action = command.split(maxsplit=2)[1].strip().lower()
        if action == "stats":
            users = _collection(db, "users")
            notes = _collection(db, "notes")
            confessions = _collection(db, "confessions")
            polls = _collection(db, "polls")
            user_count = await users.count_documents({})
            note_count = await notes.count_documents({})
            confession_count = await confessions.count_documents({})
            poll_count = await polls.count_documents({})
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
        col = _collection(db, collection)
        return await col.find_one(query, projection=projection)
    except Exception:
        logger.exception("Mongo read failed collection=%s query=%s", collection, query)
        return None


async def _ensure_user(db, payload: WhatsAppMessagePayload, now_ts: int, text: str) -> None:
    query = {"platform_id": payload.platform_id}
    if payload.phone_number:
        query = {"$or": [{"phone": payload.phone_number}, {"platform_id": payload.platform_id}]}

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
            "phone": payload.phone_number or payload.platform_id,
        },
        "$setOnInsert": {
            "created_at": now_ts,
            "first_seen": now_ts,
        },
        "$inc": {"message_count": 1},
    }

    users = _collection(db, "users")
    await users.update_one(query, user_update, upsert=True)


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

    groups = _collection(db, "groups")
    await groups.update_one({"group_id": payload.group_id}, group_update, upsert=True)


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
        logger.info("[WA][TIMING] receive_ms=%d", int((time.perf_counter() - started_at) * 1000))

        if payload.message and len(payload.message) > MAX_MESSAGE_LENGTH_FALLBACK:
            payload.message = payload.message[:MAX_MESSAGE_LENGTH_FALLBACK]
            text = _message_text(payload)

        is_sb, sb_reason = _is_status_or_broadcast(payload)
        if is_sb:
            logger.info("[WA][TIMING] ignored_status_ms=%d", int((time.perf_counter() - started_at) * 1000))
            return {"status": "ignored", "reason": sb_reason}

        lower = text.lower()
        trigger_word = should_trigger_nezuko(text)
        direct_reply = bool(payload.quoted_text) and should_trigger_nezuko(str(payload.quoted_text))
        command_trigger = _is_command(text)
        trigger_detected = trigger_word or direct_reply or command_trigger or is_cricket_request(text)
        if not trigger_detected:
            logger.info("[WA][TIMING] ignored_trigger_ms=%d", int((time.perf_counter() - started_at) * 1000))
            return {"status": "ignored", "reason": "no trigger word"}

        now_ts = payload.timestamp or int(time.time())
        is_group = payload.is_group and bool(payload.group_id)

        step_started = time.perf_counter()
        user_task = asyncio.create_task(_ensure_user(db, payload, now_ts, text))
        group_task = asyncio.create_task(_ensure_group(db, payload, now_ts)) if is_group else None
        decision_start = time.perf_counter()
        decision_task = asyncio.create_task(decide(db, payload))
        await asyncio.gather(user_task, *( [group_task] if group_task else [] ))
        decision = await decision_task
        logger.info("[WA][TIMING] mongo_user_upsert_ms=%d", int((time.perf_counter() - step_started) * 1000))
        logger.info("[WA][TIMING] memory_lookup_ms=%d", int((time.perf_counter() - started_at) * 1000))
        logger.info("[WA][TIMING] decision_ms=%d", int((time.perf_counter() - decision_start) * 1000))

        if not decision.get("allowed"):
            logger.info("[WA][TIMING] decision_blocked_ms=%d", int((time.perf_counter() - started_at) * 1000))
            return {"status": "ignored", "reason": str(decision.get("reason") or "unknown")}

        if _is_command(text):
            command_result = await _handle_slash_command(request, db, payload, text)
            if command_result is not None:
                logger.info("[WA][TIMING] command_ms=%d", int((time.perf_counter() - started_at) * 1000))
                return {"status": "success", "reply": command_result["reply"]}

        if is_cricket_request(text):
            reply = await handle_cricket_request(text, db, payload.model_dump())
            logger.info("[WA][TIMING] cricket_ms=%d", int((time.perf_counter() - started_at) * 1000))
            return {"status": "success", "reply": reply}

        if should_trigger_nezuko(text) or bool(payload.quoted_text and should_trigger_nezuko(str(payload.quoted_text))):
            command_result = await handle_nezuko_command(db, payload.model_dump(), text)
            if command_result.get("status") != "ignored":
                logger.info("[WA][TIMING] command_ms=%d", int((time.perf_counter() - started_at) * 1000))
                return {"status": "success", "reply": command_result["reply"]}

        step_started = time.perf_counter()
        reply = await generate_chat_response(text, [])
        reply = (str(reply) if reply is not None else "").strip()[:4000]
        logger.info("[WA][TIMING] prompt_ms=%d", int((time.perf_counter() - step_started) * 1000))
        logger.info("[WA][TIMING] ai_total_ms=%d", int((time.perf_counter() - started_at) * 1000))
        logger.info("[WA][TIMING] response_format_ms=%d", int((time.perf_counter() - started_at) * 1000))
        logger.info("[WA][TIMING] total_ms=%d", int((time.perf_counter() - started_at) * 1000))

        return {"status": "success", "reply": reply}

    except HTTPException:
        raise
    except Exception:
        logger.exception("WhatsApp message handling failed after_ms=%d", int((time.perf_counter() - started_at) * 1000))
        raise HTTPException(status_code=500, detail="WhatsApp integration error")



@router.get("/downloads/{download_id}/{filename}")
async def serve_download(download_id: str, filename: str):
    root = download_manager.DOWNLOADS_ROOT
    file_path = os.path.join(root, download_id, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(file_path, media_type="video/mp4", filename=filename)


@router.get("/tts/{filename}")
async def serve_tts_audio(filename: str):
    file_path = tts_service.TTS_ROOT / filename
    if file_path.parent != tts_service.TTS_ROOT or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    file_size = file_path.stat().st_size
    logger.info("[TTS] serving_audio filename=%s", filename)
    logger.info("[TTS] serving_audio size=%d", file_size)
    logger.info("[TTS] serving_audio content_type=audio/mpeg")
    return FileResponse(file_path, media_type="audio/mpeg", filename=filename)


@router.post("/tts/{filename}/complete")
async def complete_tts_audio(filename: str):
    file_path = tts_service.TTS_ROOT / filename
    if file_path.parent != tts_service.TTS_ROOT:
        raise HTTPException(status_code=404, detail="File not found")
    tts_service.remove_audio(str(file_path))
    return {"status": "ok", "deleted": not file_path.exists()}


@router.post("/downloads/{download_id}/complete")
async def complete_download(download_id: str):
    root = download_manager.DOWNLOADS_ROOT
    dir_path = os.path.join(root, download_id)
    if not os.path.exists(dir_path):
        return {"status": "ok", "deleted": False}
    try:
        shutil.rmtree(dir_path)
        logger.info("[DW] cleanup_completed download_id=%s", download_id)
        return {"status": "ok", "deleted": True}
    except Exception:
        logger.exception("[DW] cleanup_failed download_id=%s", download_id)
        raise HTTPException(status_code=500, detail="Cleanup failed")


import logging
import os
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime, timezone

from backend.database.connection import db_instance
from backend.services.nezuko import prune_expired_conversations
from backend.services.cricket_service import get_cricket_service, is_live_match, match_matches_team, _format_match
from backend.services.http_client import get_shared_http_client

logger = logging.getLogger(__name__)
_previous_cricket_state: dict[tuple[str, str], str] = {}

# Initialize the scheduler
scheduler = AsyncIOScheduler()

async def process_reminders():
    """
    Checks the database for any due reminders and 'sends' them. ⏰
    Runs every 60 seconds.
    """
    # Safety check in case the DB isn't fully loaded yet
    if db_instance.db is None:
        return

    collection = db_instance.db["reminders"]
    now = datetime.now(timezone.utc)
    
    # Find reminders that are due AND haven't been completed yet
    query = {
        "is_completed": False,
        "due_date": {"$lte": now}
    }
    
    due_reminders = await collection.find(query).to_list(length=100)
    
    if not due_reminders:
        return # Nothing to do!

    for reminder in due_reminders:
        # In a real bot, you'd send an API request to Telegram/Discord here to DM the user!
        logger.info(f"🔔 DING DING! Reminder for User {reminder['user_id']}: {reminder['title']} is DUE!")
        
        # Mark as completed so we don't spam them every minute
        await collection.update_one(
            {"_id": reminder["_id"]},
            {"$set": {"is_completed": True}}
        )

async def daily_morning_routine():
    """
    Runs every day at 8:00 AM. 
    Great place to trigger daily quotes or reset global leaderboard stats! 🌅
    """
    logger.info("🌅 Good morning campus! It's a brand new day of learning! ✨")
    # You could add logic here to generate a daily AI quote and save it to the DB


async def cleanup_conversations():
    """Prune stale Nezuko memory documents so storage remains bounded."""
    if db_instance.db is None:
        return
    deleted = await prune_expired_conversations(db_instance.db)
    if deleted:
        logger.info("🧹 Pruned %d expired conversation records", deleted)


async def process_cricket_alerts():
    """Poll once for all enabled subscribers and notify only changed matches."""
    if db_instance.db is None:
        return
    subscriptions = await db_instance.db["cricket_subscriptions"].find(
        {"feature": "cricket", "enabled": True}
    ).to_list(length=500)
    if not subscriptions:
        return

    service = get_cricket_service()
    matches = [match for match in await service.fetch_matches() if is_live_match(match)]
    if not matches:
        return
    bridge_url = os.environ.get("WHATSAPP_BRIDGE_INTERNAL_URL", "http://localhost:10000/internal/send_media")
    for subscription in subscriptions:
        chat_id = subscription.get("chat_id")
        if not chat_id:
            continue
        team = subscription.get("team")
        selected = [match for match in matches if not team or match_matches_team(match, str(team))]
        for match in selected:
            match_id = str(match.get("id") or match.get("name") or "unknown")
            fingerprint = repr((match.get("status"), match.get("score")))
            state_key = (str(chat_id), match_id)
            previous = _previous_cricket_state.get(state_key)
            _previous_cricket_state[state_key] = fingerprint
            if previous is None or previous == fingerprint:
                continue
            try:
                await get_shared_http_client().post(
                    bridge_url,
                    json={"to": chat_id, "text": f"🏏 LIVE UPDATE\n\n{_format_match(match)}"},
                    timeout=10.0,
                )
            except Exception:
                logger.exception("cricket alert delivery failed chat_id=%s", chat_id)

def start_scheduler():
    """Starts the background clock! 🕰️"""
    # Add our jobs
    scheduler.add_job(process_reminders, 'interval', minutes=1, id='check_reminders_job')
    scheduler.add_job(daily_morning_routine, 'cron', hour=8, minute=0, id='morning_routine_job')
    scheduler.add_job(cleanup_conversations, 'interval', hours=1, id='cleanup_conversations_job')
    scheduler.add_job(process_cricket_alerts, 'interval', seconds=60, id='cricket_alerts_job', max_instances=1, coalesce=True)
    
    scheduler.start()
    logger.info("⏱️ Background Scheduler started successfully! The bot never sleeps. 🦾")

def stop_scheduler():
    """Stops the clock safely."""
    scheduler.shutdown()
    logger.info("🛑 Background Scheduler stopped.")
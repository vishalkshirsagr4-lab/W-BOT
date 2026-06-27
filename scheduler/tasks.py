import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime, timezone
from database.connection import db_instance

logger = logging.getLogger(__name__)

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

def start_scheduler():
    """Starts the background clock! 🕰️"""
    # Add our jobs
    scheduler.add_job(process_reminders, 'interval', minutes=1, id='check_reminders_job')
    scheduler.add_job(daily_morning_routine, 'cron', hour=8, minute=0, id='morning_routine_job')
    
    scheduler.start()
    logger.info("⏱️ Background Scheduler started successfully! The bot never sleeps. 🦾")

def stop_scheduler():
    """Stops the clock safely."""
    scheduler.shutdown()
    logger.info("🛑 Background Scheduler stopped.")
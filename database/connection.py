import logging
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorClient

from config.settings import get_settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
settings = get_settings()


class Database:
    """Singleton holder for the shared MongoDB client and database."""

    client: Optional[AsyncIOMotorClient] = None
    db = None


db_instance = Database()


async def ensure_indexes() -> None:
    """Create a small set of high-value indexes once at startup."""
    if db_instance.db is None:
        return

    try:
        await db_instance.db["users"].create_index([("platform_id", 1)])
        await db_instance.db["users"].create_index([("phone", 1)])
        await db_instance.db["groups"].create_index([("group_id", 1)])
        await db_instance.db["blocked_users"].create_index([("phone", 1)])
        await db_instance.db["blocked_groups"].create_index([("group_id", 1)])
        await db_instance.db["chat_settings"].create_index([("chat_id", 1)])
        await db_instance.db["notes"].create_index([("subject", 1), ("type", 1), ("upvotes", -1)])
        await db_instance.db["reminders"].create_index([("user_id", 1), ("is_completed", 1), ("due_date", 1)])
        logger.info("Mongo indexes ensured")
    except Exception:
        logger.exception("Failed to ensure Mongo indexes")


async def connect_to_mongo() -> None:
    """Create a single shared MongoDB client and validate it once."""
    if db_instance.client is not None:
        return

    if not settings.MONGODB_URI:
        logger.warning("MONGODB_URI is not set; skipping Mongo connection.")
        db_instance.client = None
        db_instance.db = None
        return

    try:
        logger.info("⏳ Connecting to MongoDB...")
        db_instance.client = AsyncIOMotorClient(
            settings.MONGODB_URI,
            maxPoolSize=50,
            minPoolSize=5,
            serverSelectionTimeoutMS=3000,
            connect=False,
            retryWrites=True,
        )
        db_instance.db = db_instance.client[settings.DATABASE_NAME]

        await db_instance.client.admin.command("ping")
        await ensure_indexes()
        logger.info("✅ Successfully connected to MongoDB!")
    except Exception:
        db_instance.client = None
        db_instance.db = None
        logger.exception("❌ Could not connect to MongoDB")
        raise


async def close_mongo_connection() -> None:
    """Close the shared MongoDB connection gracefully."""
    if db_instance.client:
        db_instance.client.close()
        db_instance.client = None
        db_instance.db = None
        logger.info("🛑 MongoDB connection closed.")


def get_db():
    """FastAPI dependency returning the shared Mongo database handle."""
    return db_instance.db
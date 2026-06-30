import logging

from motor.motor_asyncio import AsyncIOMotorClient

from config.settings import get_settings


logger = logging.getLogger(__name__)
settings = get_settings()



class Database:
    """Singleton class to hold the database client and instance."""

    client: AsyncIOMotorClient | None = None
    db = None


db_instance = Database()


async def connect_to_mongo() -> None:
    """Establish connection to MongoDB."""
    try:
        logger.info("[DB] Connecting to MongoDB...")
        db_instance.client = AsyncIOMotorClient(settings.MONGODB_URI)
        db_instance.db = db_instance.client[settings.DATABASE_NAME]

        # Ping the database to verify connection
        await db_instance.client.admin.command("ping")
        logger.info("[DB] Connected successfully")
    except Exception as e:
        logger.error("[DB] Could not connect to MongoDB: %s", e)
        raise


async def close_mongo_connection() -> None:
    """Close MongoDB connection gracefully."""
    if db_instance.client:
        db_instance.client.close()
        db_instance.client = None
        db_instance.db = None
        logger.info("[DB] Connection closed")


def get_db():
    """FastAPI dependency to get the database instance."""

    return db_instance.db


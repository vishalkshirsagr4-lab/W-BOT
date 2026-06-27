import logging
from motor.motor_asyncio import AsyncIOMotorClient
from app.config.settings import get_settings

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
settings = get_settings()

class Database:
    """Singleton class to hold the database client and instance."""
    client: AsyncIOMotorClient = None
    db = None

db_instance = Database()

async def connect_to_mongo():
    """Establish connection to MongoDB."""
    try:
        logger.info("⏳ Connecting to MongoDB...")
        db_instance.client = AsyncIOMotorClient(settings.MONGODB_URI)
        db_instance.db = db_instance.client[settings.DATABASE_NAME]
        
        # Ping the database to verify connection
        await db_instance.client.admin.command('ping')
        logger.info("✅ Successfully connected to MongoDB!")
    except Exception as e:
        logger.error(f"❌ Could not connect to MongoDB: {e}")
        raise e

async def close_mongo_connection():
    """Close MongoDB connection gracefully."""
    if db_instance.client:
        db_instance.client.close()
        logger.info("🛑 MongoDB connection closed.")

def get_db():
    """
    FastAPI Dependency to get the database instance.
    Usage in routes: async def my_route(db = Depends(get_db)):
    """
    return db_instance.db
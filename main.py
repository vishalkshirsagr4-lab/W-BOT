from fastapi import FastAPI
from routes.users import router as users_router
from routes.chat import router as chat_router

from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import logging

from config.settings import get_settings
from database.connection import connect_to_mongo, close_mongo_connection


# Load settings and configure logging
settings = get_settings()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan events handle startup and shutdown logic.
    Everything before 'yield' runs on startup.
    Everything after 'yield' runs on shutdown.
    """
    # --- Startup ---
    logger.info(f"🚀 Starting {settings.PROJECT_NAME} v{settings.VERSION}...")
    await connect_to_mongo()
    yield
    # --- Shutdown ---
    logger.info("🛑 Shutting down College Community Bot...")
    await close_mongo_connection()

# Initialize FastAPI App
app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="The ultimate AI-powered college companion bot backend.",
    lifespan=lifespan
)

# Add CORS Middleware (Important if you ever build a frontend dashboard)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # In production, change this to your specific domains
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register API Routers
app.include_router(users_router, prefix="/api/v1")
app.include_router(chat_router, prefix="/api/v1")


# A simple health check route
@app.get("/", tags=["Health"])
async def root():
    return {
        "status": "online",
        "message": "Yoo! The College Community Bot backend is up and running! ✨",
        "environment": settings.ENVIRONMENT
    }



import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config.settings import get_settings
from database.connection import close_mongo_connection, connect_to_mongo
from routes.chat import router as chat_router
from routes.users import router as users_router
from routes.whatsapp import router as whatsapp_router

settings = get_settings()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Wire the shared resources once at startup and clean them up on shutdown."""
    logger.info(f"🚀 Starting {settings.PROJECT_NAME} v{settings.VERSION}...")
    app.state.http_client = httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=2.0))

    await connect_to_mongo()

    try:
        from ai.chat import init_gemini_on_startup

        await init_gemini_on_startup()
    except Exception:
        logger.exception("Gemini startup init failed")

    try:
        from scheduler.tasks import start_scheduler

        start_scheduler()
    except Exception:
        logger.warning("Scheduler start skipped/failed", exc_info=True)

    yield

    logger.info("🛑 Shutting down College Community Bot...")
    try:
        from scheduler.tasks import stop_scheduler

        stop_scheduler()
    except Exception:
        pass

    http_client = getattr(app.state, "http_client", None)
    if http_client is not None:
        await http_client.aclose()

    await close_mongo_connection()


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="The ultimate AI-powered college companion bot backend.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(users_router, prefix="/api/v1")
app.include_router(chat_router, prefix="/api/v1")
app.include_router(whatsapp_router, prefix="/api/v1")


@app.get("/", tags=["Health"])
async def root():
    return {
        "status": "online",
        "message": "Yoo! The College Community Bot backend is up and running! ✨",
        "environment": settings.ENVIRONMENT,
    }



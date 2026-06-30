from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI



from fastapi.middleware.cors import CORSMiddleware
from starlette.requests import Request

from config.settings import get_settings
from database.connection import connect_to_mongo, close_mongo_connection
from scheduler.tasks import start_scheduler, stop_scheduler
from routes.users import router as users_router
from routes.chat import router as chat_router
from routes.whatsapp import router as whatsapp_router


settings = get_settings()
logger = logging.getLogger(__name__)


# Ensure root logger is configured once at startup.
# Avoid noisy/duplicate basicConfig calls in other modules.
logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI startup/shutdown lifecycle.

    Startup:
      - MongoDB connect
      - APScheduler start

    Shutdown:
      - APScheduler stop
      - MongoDB close

    Ordering matters to avoid background jobs running while DB is torn down.
    """

    logger.info("🚀 Starting %s v%s...", settings.PROJECT_NAME, settings.VERSION)
    await connect_to_mongo()

    # Start scheduler only after Mongo is ready.
    try:
        start_scheduler()
    except Exception:
        logger.exception("Failed to start background scheduler")

    yield

    logger.info("🛑 Shutting down College Community Bot...")

    # Stop scheduler before closing DB.
    try:
        stop_scheduler()
    except Exception:
        logger.exception("Failed to stop background scheduler")

    await close_mongo_connection()


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="The ultimate AI-powered college companion bot backend.",
    lifespan=lifespan,
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, change this to your specific domains
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    # Reduce Render health-check noise: don't log HEAD /.
    if not (request.method == "HEAD" and request.url.path == "/"):
        logger.info("[REQ] %s %s", request.method, request.url.path)

    response = await call_next(request)
    return response


# Register API Routers
app.include_router(users_router, prefix="/api/v1")
app.include_router(chat_router, prefix="/api/v1")
app.include_router(whatsapp_router, prefix="/api/v1")


# A simple health check route (Render often sends HEAD /)
@app.api_route("/", methods=["GET", "HEAD"], tags=["Health"])
async def root():
    return {
        "status": "online",
        "message": "Yoo! The College Community Bot backend is up and running! ✨",
        "environment": settings.ENVIRONMENT,
    }


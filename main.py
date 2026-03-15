"""Scholaverse - FastAPI application entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import init_db
from app.middleware import AuthMiddleware
from app.routers import admin, announcements, config, generation, internal, pages, tokens


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    # Create data directory if needed
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    # Initialize database tables
    await init_db()
    yield


app = FastAPI(title="Scholaverse", version="0.1.0", lifespan=lifespan)

# Middleware
app.add_middleware(AuthMiddleware)

# Static files
app.mount("/static", StaticFiles(directory=str(settings.STATIC_DIR)), name="static")

# Routers
app.include_router(pages.router)
app.include_router(generation.router)
app.include_router(internal.router)
app.include_router(config.router)
app.include_router(tokens.router)
app.include_router(admin.router)
app.include_router(announcements.router)

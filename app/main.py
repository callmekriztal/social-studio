from contextlib import asynccontextmanager
from typing import AsyncGenerator
from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from app.api.router import api_router
from app.config import get_settings
from app.database import Base, engine
import app.models  # noqa: F401

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan manager.

    Initializes database tables on startup and handles graceful shutdown.
    """
    # Create tables on startup (can be replaced by Alembic migrations)
    Base.metadata.create_all(bind=engine)
    yield
    # Cleanup tasks on shutdown (if any)


def create_application() -> FastAPI:
    """FastAPI application factory."""
    app = FastAPI(
        title=settings.PROJECT_NAME,
        version="0.1.0",
        description="Backend studio for multi-platform social media variant generation, review, and publishing.",
        debug=settings.DEBUG,
        lifespan=lifespan,
    )

    # Mount API routers
    # Mount at root level (/health)
    app.include_router(api_router)
    # Mount with API version prefix (/api/v1/health)
    app.include_router(api_router, prefix=settings.API_V1_PREFIX)

    @app.get("/", include_in_schema=False)
    def root():
        """Redirect root requests to documentation."""
        return RedirectResponse(url="/docs")

    return app


app = create_application()

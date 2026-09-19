"""FloodGuard India — FastAPI application entry point.

Run locally:
    cd backend && uvicorn app.main:app --reload --port 8000

OpenAPI docs at http://localhost:8000/docs. The frontend's TypeScript types are
generated from this schema, so the Pydantic models are the single contract.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import health
from app.core.config import get_settings

settings = get_settings()

logging.basicConfig(
    level=getattr(logging, settings.floodguard_log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)

app = FastAPI(
    title=settings.app_name,
    version=settings.version,
    description=(
        "Dam-break / flash-flood hydrodynamic simulation and inundation mapping.\n\n"
        "Smart India Hackathon PS 26161. Every number returned by this API is computed "
        "from input data on disk; nothing is hardcoded. Engine identity is reported "
        "truthfully by GET /api/health/engines."
    ),
)

# The Vite dev server runs on 5173; the production build is served by the same
# origin, so this list exists only for local development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)


@app.get("/", include_in_schema=False)
def root() -> dict:
    return {
        "name": settings.app_name,
        "version": settings.version,
        "docs": "/docs",
        "health": "/health",
    }

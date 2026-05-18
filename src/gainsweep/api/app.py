from __future__ import annotations

from fastapi import FastAPI

from gainsweep.api.routes.alerts import router as alerts_router


def create_app() -> FastAPI:
    app = FastAPI(
        title="gainsweep",
        version="0.1.0",
        description="Crypto price-alert and sweep service for merchants",
    )

    @app.get("/api/v1/health", tags=["health"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(alerts_router, prefix="/api/v1")
    return app


app = create_app()

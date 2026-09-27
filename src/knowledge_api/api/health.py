from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import text

from .. import __version__
from ..schemas import HealthResponse

router = APIRouter(prefix="/v1/health", tags=["health"])


@router.get("/live", response_model=HealthResponse)
async def liveness(request: Request) -> HealthResponse:
    return HealthResponse(
        status="ok", service=request.app.state.settings.app_name, version=__version__
    )


@router.get("/ready", response_model=HealthResponse)
async def readiness(request: Request) -> HealthResponse:
    try:
        async with request.app.state.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="database unavailable"
        ) from exc
    return HealthResponse(
        status="ready", service=request.app.state.settings.app_name, version=__version__
    )

"""健康检查接口。"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/health")
async def health_check(request: Request) -> JSONResponse:
    """返回服务健康状态，包含 DB 和 Redis 连接检查。"""
    checks: dict[str, str] = {}
    overall = "ok"

    # 检查 Redis
    try:
        redis = getattr(request.app.state, "redis", None)
        if redis:
            await redis.ping()
            checks["redis"] = "ok"
        else:
            checks["redis"] = "not_configured"
    except Exception:
        checks["redis"] = "error"
        overall = "degraded"

    # 检查数据库
    try:
        from sqlalchemy import text
        from app.db.session import async_session_factory

        async with async_session_factory() as db:
            await db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "error"
        overall = "degraded"

    return JSONResponse(content={"status": overall, "checks": checks})

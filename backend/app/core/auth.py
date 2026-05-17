"""可选 API Key 认证依赖。"""

from fastapi import Header, HTTPException

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


async def verify_api_key(x_api_key: str | None = Header(None, alias="X-API-Key")) -> None:
    """校验 API Key。配置为空时跳过认证（开发环境）。

    用法：
        @router.post("/endpoint", dependencies=[Depends(verify_api_key)])
    """
    if not settings.api_key:
        return  # 未配置 API Key，跳过认证

    if not x_api_key:
        logger.warning("缺少 API Key")
        raise HTTPException(status_code=401, detail="缺少 X-API-Key 请求头")

    if x_api_key != settings.api_key:
        logger.warning("API Key 无效", provided=x_api_key[:8] + "***")
        raise HTTPException(status_code=401, detail="API Key 无效")

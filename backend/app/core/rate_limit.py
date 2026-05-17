"""Redis 滑动窗口限流中间件。"""

import time

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """基于 IP 的滑动窗口限流。

    使用 Redis INCR + EXPIRE 实现：
    - 每个 IP 每个时间窗口内允许的最大请求数由配置决定
    - judge 端点（LLM 调用）使用更严格的限制（1/5）
    - 超限返回 429 Too Many Requests
    """

    # 路径前缀 → 限制倍数（相对于全局限制的分数）
    STRICT_PATHS: dict[str, int] = {
        "/v1/judge/": 5,  # judge 端点限制为全局的 1/5
    }

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if not settings.rate_limit_enabled:
            return await call_next(request)

        redis = getattr(request.app.state, "redis", None)
        if redis is None:
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        path = request.url.path

        # 确定限制
        window = settings.rate_limit_window
        max_requests = settings.rate_limit_requests

        for prefix, divisor in self.STRICT_PATHS.items():
            if path.startswith(prefix):
                max_requests = max(max_requests // divisor, 1)
                break

        key = f"rate_limit:{client_ip}:{path.rstrip('/')}"

        try:
            current = await redis.incr(key)
            if current == 1:
                await redis.expire(key, window)

            if current > max_requests:
                ttl = await redis.ttl(key)
                logger.warning("限流触发", ip=client_ip, path=path, count=current, limit=max_requests)
                return Response(
                    content='{"error": "请求过于频繁，请稍后再试"}',
                    status_code=429,
                    media_type="application/json",
                    headers={"Retry-After": str(max(ttl, 1))},
                )
        except Exception:
            # Redis 不可用时放行
            logger.warning("限流 Redis 不可用，放行请求")

        return await call_next(request)

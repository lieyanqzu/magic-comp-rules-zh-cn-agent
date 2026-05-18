"""Redis 滑动窗口限流中间件。"""

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def get_client_ip(request: Request) -> str:
    """获取真实客户端 IP。

    在反向代理后（K8s ingress、ALB、nginx），request.client.host 拿到的是代理 IP，
    所有请求看起来都来自同一个 IP，限流形同虚设。
    需读 X-Forwarded-For/X-Real-IP，并按受信任的代理跳数剥层，否则可被伪造。
    """
    if settings.trust_proxy_headers:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            # XFF 格式："client, proxy1, proxy2"。从右侧剥 hops 个，取剩余最右侧。
            ips = [p.strip() for p in xff.split(",") if p.strip()]
            if ips:
                hops = max(0, settings.trusted_proxy_hops)
                idx = max(0, len(ips) - 1 - hops)
                return ips[idx]
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip.strip()

    return request.client.host if request.client else "unknown"


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

        client_ip = get_client_ip(request)
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

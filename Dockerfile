FROM python:3.12-slim AS base

WORKDIR /app

# 安装 uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# 复制后端依赖定义并安装
COPY backend/pyproject.toml backend/
RUN cd backend && uv sync --no-dev --no-install-project

# 复制后端代码
COPY backend/ backend/

# 复制规则资料
COPY skill/ skill/
COPY magic-comp-rules-zh-cn/ magic-comp-rules-zh-cn/

# 复制 entrypoint
COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

# 非 root 用户
RUN useradd -m -r appuser && chown -R appuser:appuser /app
USER appuser

# 设置环境变量
ENV PATH="/app/backend/.venv/bin:$PATH"
ENV PYTHONPATH="/app/backend"
ENV RULES_ROOT_DIR=..

WORKDIR /app/backend

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

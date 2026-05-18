# ---------- Stage 1: build frontend ----------
FROM node:20-alpine AS frontend-build

WORKDIR /build

# 先单独拷贝 manifest 利用 layer 缓存
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build
# 产物在 /build/dist

# ---------- Stage 2: backend + serve frontend ----------
FROM python:3.12-slim AS base

WORKDIR /app

# 安装 uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# 后端依赖（先单独拷贝 manifest 利用 layer 缓存）
COPY backend/pyproject.toml backend/
RUN cd backend && uv sync --no-dev --no-install-project

# 后端源码
COPY backend/ backend/

# 规则资料：skill/ 是普通目录，magic-comp-rules-zh-cn/ 是 git 子模块
COPY skill/ skill/
COPY magic-comp-rules-zh-cn/ magic-comp-rules-zh-cn/

# 子模块完整性检查：Zeabur / 任何 PaaS 如果没有 --recurse-submodules
# 拉代码，magic-comp-rules-zh-cn/ 会是空目录，这里 fail-fast，避免部署一个
# 检索不到任何规则的废镜像。验证标志：根目录必须有 markdown/ 子目录。
RUN test -d magic-comp-rules-zh-cn/markdown \
    || (echo "ERROR: git submodule magic-comp-rules-zh-cn 未初始化。" \
        && echo "本地：git submodule update --init --recursive" \
        && echo "PaaS：确认部署时拉取了子模块（--recurse-submodules）" \
        && exit 1)

# 前端 dist 拷进来，由 FastAPI mount 成 /
COPY --from=frontend-build /build/dist /app/frontend-dist

# entrypoint
COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

# 非 root 用户
RUN useradd -m -r appuser && chown -R appuser:appuser /app
USER appuser

ENV PATH="/app/backend/.venv/bin:$PATH"
ENV PYTHONPATH="/app/backend"
ENV RULES_ROOT_DIR=..
# 静态资源根目录：FastAPI 启动时读这个变量决定 mount 哪个目录
ENV FRONTEND_DIST_DIR=/app/frontend-dist

WORKDIR /app/backend

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

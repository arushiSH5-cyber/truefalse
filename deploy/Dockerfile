# TrueFalse in one container for Hugging Face Spaces (or any Docker host).
# The web app listens on 7860; it proxies /backend/api and /webhooks to the checker on 8000.
FROM node:20-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
ENV NEXT_TELEMETRY_DISABLED=1 API_URL=http://127.0.0.1:8000
RUN npx next build

FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.13 /uv /bin/
COPY --from=web /usr/local/bin/node /usr/local/bin/node
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-noto-core fonts-noto-cjk libraqm0 curl \
    && rm -rf /var/lib/apt/lists/*

# Spaces run the container as user 1000.
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy NEXT_TELEMETRY_DISABLED=1 \
    APP_ENV=prod LOG_LEVEL=INFO
WORKDIR /home/user/app

COPY --chown=user backend/pyproject.toml backend/uv.lock backend/
RUN cd backend && uv sync --python 3.12 --frozen --no-dev --extra media --no-install-project
COPY --chown=user backend/ backend/
RUN cd backend && uv sync --python 3.12 --frozen --no-dev --extra media
COPY --chown=user --from=web /web web
COPY --chown=user deploy/start.sh start.sh

EXPOSE 7860
CMD ["bash", "start.sh"]

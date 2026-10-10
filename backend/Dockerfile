FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /uvx /bin/

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
# Pass --build-arg EXTRAS="--extra ml" to include the embedding model runtime (large).
ARG EXTRAS=""
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH="/app/.venv/bin:$PATH"

COPY pyproject.toml uv.lock* ./
RUN uv sync --no-install-project --no-dev $EXTRAS

COPY . .
RUN uv sync --no-dev $EXTRAS

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

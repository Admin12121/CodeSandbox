FROM python:3.12-slim

# Install uv (same approach as the main app's Dockerfile.dev)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /worker

COPY pyproject.toml .
RUN uv sync --no-dev

COPY worker.py main.py ./
COPY runtime ./runtime

CMD ["uv", "run", "python", "-u", "main.py"]

FROM ghcr.io/astral-sh/uv:python3.12-slim

WORKDIR /worker

COPY pyproject.toml .
RUN uv sync --no-dev

COPY worker.py .

CMD ["uv", "run", "python", "-u", "worker.py"]

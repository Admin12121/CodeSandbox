FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /worker

# There is no uv.lock, so resolve dependencies from pyproject.toml
# during the image build. Do not use --frozen.
COPY pyproject.toml ./
RUN uv sync --no-dev --no-install-project

COPY worker.py main.py ./
COPY runtime ./runtime

ENV PATH="/worker/.venv/bin:${PATH}"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

CMD ["/worker/.venv/bin/python", "-u", "main.py"]
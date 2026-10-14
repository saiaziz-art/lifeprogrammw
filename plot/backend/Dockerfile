FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends libpq5 ca-certificates && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY plot ./plot
COPY alembic ./alembic
COPY alembic.ini ./
RUN pip install ".[ai]"

RUN useradd --create-home --uid 10001 plot
USER plot

# One image, three roles: `api`, `worker`, `scheduler` (see docker-compose.yml)
ENTRYPOINT ["sh", "-c", "plot migrate && plot seed && exec \"$@\"", "--"]
CMD ["plot", "api", "--host", "0.0.0.0", "--port", "8000"]

# api/ backend image. Builds the FastAPI app for production; expects
# DATABASE_URL to point at a real PostgreSQL instance (see
# docker-compose.yml, which wires this to the `db` service).
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run as an unprivileged user.
RUN useradd --create-home --uid 10001 app && chown -R app /app
USER app

EXPOSE 8000

# Apply migrations, then serve. ONE worker on purpose: sessions and the
# login rate limiter are in-process (see SECURITY.md); scale out only after
# moving them to Redis.
CMD ["sh", "-c", "alembic upgrade head && uvicorn api.main:app --host 0.0.0.0 --port 8000 --workers 1"]

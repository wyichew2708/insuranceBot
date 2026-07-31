FROM python:3.11-slim
RUN useradd --uid 10001 --create-home appuser
RUN pip install --no-cache-dir alembic sqlalchemy "psycopg[binary]"
WORKDIR /app
COPY db ./db
USER 10001
WORKDIR /app/db
CMD alembic upgrade head

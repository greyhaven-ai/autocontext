FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY mts /app/mts

RUN pip install --no-cache-dir -e "/app/mts[dev]"

ENTRYPOINT ["mts"]

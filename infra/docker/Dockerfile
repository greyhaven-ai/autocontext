FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY autocontext /app/autocontext

RUN pip install --no-cache-dir -e "/app/autocontext[dev]"

ENTRYPOINT ["autoctx"]

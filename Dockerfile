# --- stage 1: build wheels ---
FROM python:3.12-slim AS builder

WORKDIR /app

RUN pip install --upgrade pip

COPY requirements.txt .
RUN pip wheel --no-cache-dir --no-deps --wheel-dir /app/wheels -r requirements.txt

# --- stage 2: production image ---
FROM python:3.12-slim

WORKDIR /app

COPY --from=builder /app/wheels /wheels
COPY --from=builder /app/requirements.txt .
RUN pip install --no-cache /wheels/*

COPY . .

EXPOSE 5000

ENV FLASK_APP=app.py
ENV FLASK_RUN_HOST=0.0.0.0

# 600s timeout to cover long Overpass API runs across many chunks
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--timeout", "600", "app:app"]

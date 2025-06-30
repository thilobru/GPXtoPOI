# Phase 1: Build-Umgebung
# Verwendet ein schlankes Python-Image
FROM python:3.12-slim AS builder

# Setzt das Arbeitsverzeichnis
WORKDIR /app

# Installiert Build-Abhängigkeiten
RUN pip install --upgrade pip

# Kopiert die requirements.txt und installiert die Python-Pakete
COPY requirements.txt .
RUN pip wheel --no-cache-dir --no-deps --wheel-dir /app/wheels -r requirements.txt

# Phase 2: Produktions-Umgebung
# Verwendet dasselbe schlanke Image
FROM python:3.12-slim

# Setzt das Arbeitsverzeichnis
WORKDIR /app

# Kopiert die vorkompilierten Pakete aus der Build-Umgebung
COPY --from=builder /app/wheels /wheels
COPY --from=builder /app/requirements.txt .
RUN pip install --no-cache /wheels/*

# Kopiert den Anwendungscode in den Container
COPY . .

# Gibt den Port frei, auf dem die Anwendung laufen wird
EXPOSE 5000

# Setzt Umgebungsvariablen für Flask
ENV FLASK_APP=app.py
ENV FLASK_RUN_HOST=0.0.0.0

# Der Befehl, der beim Starten des Containers ausgeführt wird
# Gunicorn ist ein robusterer Produktionsserver als der Flask-Entwicklungsserver
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "app:app"]

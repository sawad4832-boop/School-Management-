# Fuer den Dauerbetrieb auf einem kleinen Server (Raspberry Pi, VPS, Hoster).
# Bauen und starten:
#   docker build -t schulcloud-dashboard .
#   docker run -p 5000:5000 --env-file .env -v "$PWD/data:/app/data" schulcloud-dashboard
#
# Wichtig: nur EIN Worker. Die Sessions (inkl. JWT) liegen absichtlich im
# Arbeitsspeicher des Prozesses und wuerden sich sonst nicht wiederfinden.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn==23.0.0

COPY . .

ENV HOST=0.0.0.0 PORT=5000 SC_DB_PATH=/app/data/dashboard.sqlite3
EXPOSE 5000
VOLUME ["/app/data"]

CMD ["gunicorn", "--workers", "1", "--threads", "4", "--bind", "0.0.0.0:5000", "app:app"]

# LAS Dashboard Middleware (backend only; the UI is the static frontend/dist).
#
#   docker build -t las-backend .
#   docker run -d --name las-backend --restart unless-stopped \
#     --env-file /opt/las/.env -p 127.0.0.1:8000:8000 las-backend
#
# Settings come from the environment at run time (--env-file); .env is never
# copied into the image. Publish the port on 127.0.0.1 only: nginx on the host
# is the one public entry point (deploy/nginx-las.conf).
#
# Port: the app listens on $PORT (default 8000). To use another one, set
# PORT=9000 in the env file, publish -p 127.0.0.1:9000:9000, and point
# proxy_pass in deploy/nginx-las.conf at 127.0.0.1:9000.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first, so a code change does not reinstall them. The lock pins
# every package with its hashes; --require-hashes makes a tampered or unpinned
# package a hard error.
COPY requirements.txt .
RUN pip install --require-hashes -r requirements.txt

COPY pyproject.toml .
COPY app ./app
# --no-deps: the dependencies are the locked ones above, not re-resolved.
RUN pip install --no-deps .

RUN useradd --system --no-create-home las
USER las

ENV PORT=8000
EXPOSE 8000

# PORT and CONTEXT_PATH are read at run time, so the probe follows them.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f\"http://127.0.0.1:{os.environ['PORT']}\" + os.environ.get('CONTEXT_PATH', '').rstrip('/') + '/health', timeout=4)"

# One worker: the super-admin token is cached in process memory
# (app/services/admin_session.py); more workers would each sign in separately.
# Shell form so $PORT expands; exec keeps uvicorn as PID 1 for clean stops.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port \"$PORT\" --workers 1"]

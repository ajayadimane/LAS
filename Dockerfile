# LAS Dashboard Middleware (backend only; the UI is the static frontend/dist).
# One image, two containers (docker-compose.yaml, deploy/DEPLOYMENT.md):
#   las             the API (default command)
#   las-dpr-worker  the DPR analysis worker: same image, command
#                   `python scripts/dpr_worker.py`
#
# Without compose:
#   docker build -t las-backend .
#   docker run -d --name las-backend --restart unless-stopped --network host \
#     --env-file /opt/las/.env las-backend
#
# Settings come from the environment at run time (--env-file); .env is never
# copied into the image. --network host lets the containers reach Postgres on
# the host at 127.0.0.1; set HOST=127.0.0.1 in the env file so the API listens
# on loopback only: nginx on the host is the one public entry point
# (deploy/nginx-las.conf). PORT (default 8000) is the port it listens on.

# Python 3.10: the version the test suite runs on and requirements.txt was
# locked from. Debian pinned (bookworm) so the apt package names below hold.
FROM python:3.10-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# WeasyPrint (the DPR/CAM report PDF) needs Pango + HarfBuzz; the report's own
# fonts ship in app/templates/cam/fonts, DejaVu is the fallback for any glyph
# they lack. LiteParse (Node + ImageMagick + Tesseract) is deliberately not
# installed: without it the worker parses documents with Gemini vision, which
# is how it has been run and tested so far.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libharfbuzz-subset0 \
        fontconfig fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

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

# Core's Postgres migrations: the installed package cannot find them from its
# own path, so point at the copy here. Run them as the schema owner on each
# deploy: docker run --rm --network host --env-file ... las-backend python scripts/migrate.py
COPY migrations ./migrations
COPY scripts/migrate.py scripts/dpr_worker.py ./scripts/
ENV LAS_MIGRATIONS_DIR=/app/migrations

RUN useradd --system --no-create-home las
USER las

ENV HOST=0.0.0.0 \
    PORT=8000
EXPOSE 8000

# HOST, PORT and CONTEXT_PATH are read at run time, so the probe follows them.
# (The worker container has no HTTP: run it with --no-healthcheck.)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os, urllib.request; h = os.environ['HOST']; h = '127.0.0.1' if h == '0.0.0.0' else h; urllib.request.urlopen(f\"http://{h}:{os.environ['PORT']}\" + os.environ.get('CONTEXT_PATH', '').rstrip('/') + '/health', timeout=4)"

# One worker: the super-admin token is cached in process memory
# (app/services/admin_session.py); more workers would each sign in separately.
# Shell form so $HOST/$PORT expand; exec keeps uvicorn as PID 1 for clean stops.
CMD ["sh", "-c", "exec uvicorn app.main:app --host \"$HOST\" --port \"$PORT\" --workers 1"]

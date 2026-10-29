# LAS dashboard: demo deployment

Two parts, one public address, served by the server's existing nginx:

```
Browser ─► http://<demo-host>/las/       ─► nginx serves static files from /var/www/las/
        └► http://<demo-host>/las-api/   ─► nginx ─► backend container on 127.0.0.1:9000
                                                        └► iWallet vendor (https://10.10.20.153:8080)
```

| Part | What it is | Where it goes on the server |
|---|---|---|
| Frontend | Static files, already built (`frontend/dist/`). No Node.js needed. | `/var/www/las/` |
| Backend | Python FastAPI app, run as a Docker container | Code in `/opt/las/`, container on port 9000 |

The paths `/las` and `/las-api` are fixed into the frontend build. If you
need different paths, ask the dev team for a rebuild. Don't edit `dist/`.

---

## 0. Before you start (one-time checks)

On the demo server:

```bash
docker --version          # Docker installed
nginx -v                  # nginx installed
sudo ss -ltnp | grep 9000 # port 9000 must be free (no output = free)
```

The server must reach the vendor, which is on the office network:

```bash
curl -k -s -o /dev/null -w "%{http_code}\n" https://10.10.20.153:8080
```

`404` is good: the vendor answered. A timeout means the server can't reach
it. Fix the network before going further, or nothing will work.

---

## 1. What you receive

From the dev team:

1. **`las-deploy.tar.gz`**, which contains:
   - `app/`, `pyproject.toml`, `requirements.txt`: the backend code
   - `Dockerfile`, `.dockerignore`: the backend image
   - `frontend/dist/`: the built UI
   - `deploy/`: this guide and `nginx-las.conf`
2. **The backend settings** (`.env` values), sent separately and privately.
   They include the vendor admin password. Never commit them, put them in
   the image, or paste them in chat.

---

## 2. Unpack

```bash
sudo mkdir -p /opt/las
sudo tar -xzf las-deploy.tar.gz -C /opt/las --strip-components=1
ls /opt/las        # app  deploy  Dockerfile  frontend  pyproject.toml  requirements.txt ...
```

---

## 3. Frontend: copy the built UI

```bash
sudo mkdir -p /var/www/las
sudo cp -r /opt/las/frontend/dist/. /var/www/las/
sudo chown -R www-data:www-data /var/www/las     # nginx's user; `nginx` on RHEL/CentOS
ls /var/www/las    # index.html  assets/
```

---

## 4. Backend: create the settings file

```bash
sudo nano /opt/las/.env
```

Contents (fill in the two admin values from the dev team):

```
ENVIRONMENT=demo
LOG_LEVEL=INFO
PORT=9000
CONTEXT_PATH=/las-api

VENDOR_BASE_URL=https://10.10.20.153:8080
VENDOR_VERIFY_SSL=false

VENDOR_ADMIN_USER_ID=<from dev team>
VENDOR_ADMIN_PASSWORD=<from dev team>
```

Lock it down. It holds a password:

```bash
sudo chmod 600 /opt/las/.env
```

All other settings have working defaults; `/opt/las/.env.example` lists them.
`CONTEXT_PATH` must stay `/las-api`, because the frontend build calls that path.

---

## 5. Backend: build and start the container

```bash
cd /opt/las
sudo docker build -t las-backend:$(date +%Y%m%d) -t las-backend:latest .

sudo docker run -d --name las-backend --restart unless-stopped \
  --env-file /opt/las/.env \
  -p 127.0.0.1:9000:9000 \
  las-backend:latest
```

- `-p 127.0.0.1:9000:9000` keeps the backend reachable **only from this
  server**. nginx is the one public entry point. Don't use `-p 9000:9000`.
- `--restart unless-stopped` brings it back after a crash or a reboot.
- The date tag keeps the previous image around for rollback (step 9).

Check it:

```bash
sudo docker ps --filter name=las-backend      # STATUS: Up ... (healthy) after ~30 s
sudo docker logs las-backend | tail           # "Uvicorn running on http://0.0.0.0:9000"
curl http://127.0.0.1:9000/las-api/health     # {"status":"ok",...,"environment":"demo"}
```

Check that the container itself reaches the vendor (expect `404`):

```bash
sudo docker exec las-backend python -c "import httpx; print(httpx.get('https://10.10.20.153:8080', verify=False).status_code)"
```

---

## 6. nginx: add the two locations

Open the site that serves the demo host (often
`/etc/nginx/sites-available/default` or a file in `/etc/nginx/conf.d/`), and
inside its `server { ... }` block add:

```nginx
include /opt/las/deploy/nginx-las.conf;
```

(or paste that file's contents there). Then:

```bash
sudo nginx -t                    # must say "syntax is ok" and "test is successful"
sudo systemctl reload nginx
```

`nginx-las.conf` assumes `/var/www/las/` and port `9000`. If you changed
either, edit the `alias` lines or `proxy_pass` to match.

---

## 7. Verify end to end

From your own machine's browser:

| Check | Expected |
|---|---|
| `http://<demo-host>/las-api/health` | `{"status":"ok", ..., "environment":"demo"}` |
| `http://<demo-host>/las/` | The LAS sign-in page |
| Sign in as a BD | Customer list loads |
| Open a customer page, press F5 | Page reloads (not a 404) |
| Upload a document over 1 MB | Succeeds (not `413`) |

If all five pass, the deployment is done.

---

## 8. Updating later

**Backend changed:**

```bash
cd /opt/las            # unpack the new code over the old (keep .env)
sudo docker build -t las-backend:$(date +%Y%m%d) -t las-backend:latest .
sudo docker rm -f las-backend
sudo docker run -d --name las-backend --restart unless-stopped \
  --env-file /opt/las/.env -p 127.0.0.1:9000:9000 las-backend:latest
```

**Frontend changed** (dev team sends a new `dist/`):

```bash
sudo rm -rf /var/www/las/*
sudo cp -r <new dist>/. /var/www/las/
```

No nginx reload is needed. Users get the new UI on their next page load.

**A setting changed** (`.env`): `sudo docker restart las-backend` is **not**
enough, because `--env-file` is read only when the container is created.
Run the `docker rm -f` and `docker run` lines from "Backend changed" above.

---

## 9. Rollback

```bash
sudo docker images las-backend                  # pick the previous date tag
sudo docker rm -f las-backend
sudo docker run -d --name las-backend --restart unless-stopped \
  --env-file /opt/las/.env -p 127.0.0.1:9000:9000 las-backend:<previous-date>
```

For the frontend, keep a copy of the old `/var/www/las` before replacing it.

---

## 10. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `502 Bad Gateway` on `/las-api/...` | Backend not running or wrong port. `docker ps`, `docker logs las-backend`; `proxy_pass` port must equal `PORT`. |
| `404` on `/las-api/health` but container is up | `CONTEXT_PATH` in `.env` is not `/las-api`. Recreate the container after fixing (step 8). |
| `/las/` shows a blank page | Files not under `/var/www/las/`, or wrong `alias`. Browser dev tools: `/las/assets/...` should load with 200. |
| `403 Forbidden` on `/las/` | nginx can't read `/var/www/las`. Re-run the `chown` in step 3. |
| Refreshing an inner page gives `404` | The `try_files` line from `nginx-las.conf` is missing. |
| Upload fails with `413` | `client_max_body_size 5m;` missing from the `/las-api/` location. |
| Sign-in says vendor unreachable / timeout | Server or container can't reach `10.10.20.153:8080` (step 0 / step 5 checks). |
| Sign-in fails with an SSL/certificate error | `VENDOR_VERIFY_SSL=false` missing from `.env`. |
| Adding a customer fails with `ADMIN_...` | `VENDOR_ADMIN_USER_ID` / `VENDOR_ADMIN_PASSWORD` missing or wrong. |
| Container keeps restarting | `docker logs las-backend`: usually a typo in `.env`. |

Logs: `sudo docker logs -f las-backend` (backend; every vendor call is logged
with its status and duration) and `/var/log/nginx/error.log` (nginx).

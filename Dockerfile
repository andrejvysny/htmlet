# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------- #
# Multi-stage build: deps are resolved + installed into a throwaway venv in the
# `build` stage; the final stage copies ONLY that venv, so neither the uv binary
# (~35 MB) nor any build scratch ends up in the shipped image. Result: ~66 MB
# (Alpine/musl) vs ~222 MB for the old single-stage python:3.12-slim build.
#
# Runtime deps are a curated SUBSET of fastapi[standard]: the image runs uvicorn
# directly (see CMD), so it does NOT need the fastapi CLI tree (fastapi-cli,
# typer, rich, sentry-sdk), Jinja2 (templates are served via HTMLResponse),
# email-validator, httpx, websockets, or watchfiles. pyproject.toml keeps
# fastapi[standard] for the local `fastapi dev` workflow; only the image is
# trimmed. python-multipart IS required — it backs request.form()/UploadFile.
#
# NOTE: this installs latest-compatible versions (no uv.lock / --require-hashes).
# To restore a pinned, hash-verified supply chain, generate a runtime lockfile:
#   printf 'fastapi\nuvicorn\npython-multipart\n' \
#     | uv pip compile --generate-hashes - -o requirements-runtime.txt
# then below: uv pip install --no-cache --require-hashes -r requirements-runtime.txt
# ---------------------------------------------------------------------------- #
FROM python:3.13-alpine AS build

# uv for fast installs. Optional hardening: pin by digest, e.g.
#   COPY --from=ghcr.io/astral-sh/uv:0.9@sha256:... /uv /bin/uv
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /bin/uv

# Don't let uv fetch a managed CPython — build the venv against the image's own
# interpreter so it stays ABI-compatible with the runtime stage below.
ENV UV_PYTHON_DOWNLOADS=0
RUN uv venv /opt/venv \
    && VIRTUAL_ENV=/opt/venv uv pip install --no-cache \
        fastapi uvicorn python-multipart

# ---------------------------------------------------------------------------- #
FROM python:3.13-alpine

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    DATA_DIR=/data \
    PATH=/opt/venv/bin:$PATH

# The self-contained venv from the build stage is the entire dependency payload.
COPY --from=build /opt/venv /opt/venv

WORKDIR /app
COPY app ./app
# Agent skill served at /skill.md so agents can be set up from a live instance.
COPY skills ./skills

# Non-root runtime + writable data volume.
RUN adduser -D -u 10001 app \
    && mkdir -p /data \
    && chown -R app:app /data
USER app

VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz').status==200 else 1)"

# --proxy-headers honors X-Forwarded-* but only from trusted hops. uvicorn
# defaults forwarded_allow_ips to 127.0.0.1; override with the FORWARDED_ALLOW_IPS
# env var to trust your reverse proxy (see docker-compose.yml). Do NOT use "*"
# on a directly-exposed instance — it lets any client spoof the host in returned
# URLs and the source IP in logs. Setting BASE_URL also neutralizes host spoofing.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers"]

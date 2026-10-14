# syntax=docker/dockerfile:1.7
#
# ANA web product — production image (docs/DEPLOY.md).
#
#   docker build -t ana:local .
#   docker run -d -p 127.0.0.1:8000:8000 -v ana-data:/data \
#     -e ANA_SECRET_KEY="$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')" ana:local
#
# Stage 1 builds a wheel from the source tree and installs it (non-editable,
# with the `llm` and `postgres` extras) into a self-contained virtualenv.
# Stage 2 copies only that virtualenv: no source tree, tests, docs, .git,
# build tools or pip in the final image.
#
# Behind a TLS-intercepting proxy, pass its CA bundle as a build secret (it is
# only mounted for the RUN step and never stored in a layer):
#   docker build --secret id=pip_ca,src=/path/to/ca-bundle.crt -t ana:local .

ARG PYTHON_IMAGE=python:3.12-slim-bookworm

# ─────────────────────────── build ───────────────────────────
FROM ${PYTHON_IMAGE} AS build

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /src
COPY pyproject.toml README.md ./
COPY ana ./ana

RUN --mount=type=secret,id=pip_ca,required=false \
    set -eu; \
    if [ -s /run/secrets/pip_ca ]; then export PIP_CERT=/run/secrets/pip_ca; fi; \
    pip wheel --no-deps --wheel-dir /wheels . ; \
    python -m venv --without-pip /opt/venv; \
    pip --python /opt/venv/bin/python install "$(ls /wheels/ana_engine-*.whl)[llm,postgres]"; \
    /opt/venv/bin/python -c "import ana.app.main as m; assert (m.STATIC_DIR / 'index.html').is_file(), 'static assets missing from wheel'"

# ────────────────────────── runtime ──────────────────────────
FROM ${PYTHON_IMAGE} AS runtime

LABEL org.opencontainers.image.title="ana" \
      org.opencontainers.image.description="ANA web product (FastAPI + uvicorn, single worker)" \
      org.opencontainers.image.licenses="Proprietary"

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ANA_DATABASE_URL=sqlite:////data/ana.db \
    ANA_SHARD_DB=/data/shards.db \
    PORT=8000 \
    FORWARDED_ALLOW_IPS=127.0.0.1

# The runtime needs neither pip nor setuptools/wheel (the venv has no pip either).
RUN rm -rf /usr/local/lib/python3.12/site-packages/pip* \
           /usr/local/lib/python3.12/site-packages/setuptools* \
           /usr/local/lib/python3.12/site-packages/wheel* \
           /usr/local/bin/pip* \
 && groupadd --system --gid 10001 ana \
 && useradd --system --uid 10001 --gid 10001 --no-create-home --home-dir /app \
            --shell /usr/sbin/nologin ana \
 && mkdir -p /app /data \
 && chown 10001:10001 /app /data

COPY --from=build /opt/venv /opt/venv

WORKDIR /app
USER 10001:10001
VOLUME /data
EXPOSE 8000

# /healthz through the app's Host allowlist: send "localhost" when it is allowed
# (the default), otherwise the first entry of ANA_ALLOWED_HOSTS. No curl needed.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD ["python", "-c", "import os,sys,urllib.request as u\nhs=[h.strip() for h in (os.environ.get('ANA_ALLOWED_HOSTS') or 'localhost').split(',') if h.strip()] or ['localhost']\nh='localhost' if 'localhost' in hs else hs[0]\nr=u.Request('http://127.0.0.1:%s/healthz' % os.environ.get('PORT','8000'), headers={'Host': h})\ntry:\n    sys.exit(0 if u.build_opener(u.ProxyHandler({})).open(r, timeout=4).status == 200 else 1)\nexcept Exception:\n    sys.exit(1)"]

# One worker on purpose: journeys live in an in-process cache with per-journey
# locks (docs/DEPLOY.md, "Limits of v1"). FORWARDED_ALLOW_IPS lists the proxy
# addresses whose X-Forwarded-For/-Proto are trusted ("*" only when the port is
# reachable from the proxy alone).
CMD ["sh", "-c", "exec uvicorn ana.app.main:app --host 0.0.0.0 --port \"${PORT:-8000}\" --workers 1 --proxy-headers --forwarded-allow-ips \"${FORWARDED_ALLOW_IPS:-127.0.0.1}\" --no-server-header"]

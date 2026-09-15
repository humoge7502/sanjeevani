# SANJEEVANI — one deployable image containing the API and the command center.
#
#   docker build -t sanjeevani .
#   docker run --rm -p 8787:8787 -v sanjeevani-ledger:/app/runtime sanjeevani
#
# Two stages so the runtime image never contains Node, the npm cache, or the
# frontend's dev toolchain. Only the built static assets cross the boundary.

# ---------------------------------------------------------------------------
# Stage 1 — build the command center
# ---------------------------------------------------------------------------
FROM node:20-alpine AS web

WORKDIR /build

# Copy manifests first so the dependency layer is cached independently of source
# changes. `npm ci` (not `install`) so the build is reproducible from the lockfile.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
# `npm run build` runs `tsc -b && vite build`, so a type error fails the image
# build rather than shipping. The e2e specs are typechecked here too.
RUN npm run build


# ---------------------------------------------------------------------------
# Stage 2 — runtime
# ---------------------------------------------------------------------------
FROM python:3.13-slim AS runtime

# No .pyc writing and unbuffered output: the right defaults for a container,
# where stdout is the log stream and the layer is immutable.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first, for the same layer-caching reason as stage 1.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Application. `docs/` and `tests/` are deliberately absent: nothing reads them
# at runtime, and docs/ carries ~3 MB of source PDFs.
COPY backend/ ./backend/
COPY config/ ./config/
COPY data/ ./data/
COPY scripts/ ./scripts/
COPY --from=web /build/dist ./frontend/dist

# The ledger is the only mutable state. Owned by the runtime user so the
# write check in /api/health passes without a mounted volume too.
RUN mkdir -p /app/runtime \
    && useradd --create-home --uid 10001 sanjeevani \
    && chown -R sanjeevani:sanjeevani /app/runtime

USER sanjeevani

ENV SANJEEVANI_API_HOST=0.0.0.0 \
    SANJEEVANI_API_PORT=8787 \
    SANJEEVANI_RUNTIME_DIR=/app/runtime

EXPOSE 8787

# Persist the audit ledger across container restarts. Without this the hash chain
# is lost on every redeploy, which would undermine the auditability claim.
VOLUME ["/app/runtime"]

# Hits the same endpoint the UI uses, so the probe exercises the real app rather
# than a static file. start-period covers interpreter + import time.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request,sys; \
port=os.environ.get('SANJEEVANI_API_PORT','8787'); \
sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{port}/api/health',timeout=4).status==200 else 1)"

# Single process. The orchestrator and ledger are in-process singletons, so a
# second worker would fork the approval state. See scripts/serve.py.
CMD ["python", "scripts/serve.py"]

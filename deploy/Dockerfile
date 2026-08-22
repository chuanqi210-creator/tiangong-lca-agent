FROM python:3.11.15-slim@sha256:db3ff2e1800a8581e2c48a27c3995339d47bdf046da21c7627accd3d51053a93

ARG TARGETARCH

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONNOUSERSITE=1 \
    PYTHONPATH=/app \
    D18_SERVICE_DATA_ROOT=/var/lib/d18 \
    D18_SERVICE_HOST=0.0.0.0 \
    D18_SERVICE_BEARER_TOKEN_FILE=/run/secrets/d18_service_token

RUN apt-get update \
    && mkdir -p /tmp/d18-package \
    && cd /tmp/d18-package \
    && apt-get download libseccomp2=2.6.0-2

WORKDIR /app

COPY runtime/service-python/requirements.lock /app/runtime/service-python/requirements.lock
COPY lca_research_service/container_source_lock.py /app/lca_research_service/container_source_lock.py
COPY deploy/container-source-lock.json /app/deploy/container-source-lock.json

RUN python /app/lca_research_service/container_source_lock.py \
        --repository-root /app \
        --lock /app/deploy/container-source-lock.json \
        --architecture "$TARGETARCH" \
        --package-artifact "/tmp/d18-package/libseccomp2_2.6.0-2_${TARGETARCH}.deb" \
    && apt-get install --yes --no-install-recommends \
        "/tmp/d18-package/libseccomp2_2.6.0-2_${TARGETARCH}.deb" \
    && groupadd --system --gid 10001 d18 \
    && useradd --system --uid 10001 --gid d18 --home-dir /nonexistent d18

RUN python -m pip install --no-cache-dir --require-hashes \
    -r /app/runtime/service-python/requirements.lock

COPY lca_research_kernel /app/lca_research_kernel
COPY lca_research_service /app/lca_research_service
COPY scripts/check_d18_container_runtime.py /app/scripts/check_d18_container_runtime.py
COPY docs/lca-agent-top-level-architecture.json /app/docs/lca-agent-top-level-architecture.json
COPY docs/superpowers/specs/2026-07-24-d18-performance-parity-and-backend-closure-design.md /app/docs/superpowers/specs/2026-07-24-d18-performance-parity-and-backend-closure-design.md
COPY docs/superpowers/plans/2026-07-24-d18-performance-parity-and-backend-closure.md /app/docs/superpowers/plans/2026-07-24-d18-performance-parity-and-backend-closure.md
COPY runtime/model-python/requirements.lock /app/runtime/model-python/requirements.lock
COPY runtime/model-python/source-lock.json /app/runtime/model-python/source-lock.json
COPY runtime/service-python/source-lock.json /app/runtime/service-python/source-lock.json

RUN mkdir -p /var/lib/d18 \
    && chown -R d18:d18 /var/lib/d18 /app \
    && rm -rf /tmp/d18-package /var/lib/apt/lists/*

USER 10001:10001

RUN python -c "from lca_research_service.material_sandbox import probe_material_runtime; assert probe_material_runtime()['ready']" \
    && python -W ignore::RuntimeWarning -m lca_research_service.material_sandbox

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import json,urllib.request; assert json.load(urllib.request.urlopen('http://127.0.0.1:8080/health/ready', timeout=3))['status']=='ready'"]

CMD ["python", "-m", "lca_research_service"]

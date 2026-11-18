FROM python:3.11-slim@sha256:07d4824036af339fa1b6ef769cc99d9c10a141769538b52b3b597c84f7a7b535 AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

COPY infra/docker/build-requirements.lock /tmp/build-requirements.lock
COPY docs /build/docs
COPY autocontext /build/autocontext

RUN python -m pip install --require-hashes -r /tmp/build-requirements.lock \
    && python -m pip wheel --no-build-isolation --no-deps \
        --wheel-dir /tmp/wheels /build/autocontext

FROM python:3.11-slim@sha256:07d4824036af339fa1b6ef769cc99d9c10a141769538b52b3b597c84f7a7b535 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY infra/docker/requirements.lock /tmp/requirements.lock
COPY --from=builder /tmp/wheels /tmp/wheels

RUN python -m pip install --require-hashes -r /tmp/requirements.lock \
    && python -m pip install --no-deps --no-index /tmp/wheels/autocontext-*.whl \
    && addgroup --system --gid 10001 autocontext \
    && adduser --system --uid 10001 --ingroup autocontext --home /workspace autocontext \
    && mkdir -p /workspace/runs /workspace/knowledge /workspace/skills \
    && chown -R autocontext:autocontext /workspace \
    && rm -rf /tmp/wheels /tmp/requirements.lock

WORKDIR /workspace
USER 10001:10001

ENTRYPOINT ["autoctx"]

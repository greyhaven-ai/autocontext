ARG PYTHON_IMAGE=python:3.11-slim@sha256:be1575ed968de893bd54f4c56315ff7c4736ce522c1bca08fd521731aafc0d76

FROM ${PYTHON_IMAGE} AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

COPY infra/docker/build-requirements.lock /tmp/build-requirements.lock
COPY docs /build/docs
COPY autocontext /build/autocontext

RUN python -m pip install --require-hashes -r /tmp/build-requirements.lock \
    && python -m pip wheel --no-build-isolation --no-deps \
        --wheel-dir /tmp/wheels /build/autocontext

FROM ${PYTHON_IMAGE} AS runtime

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

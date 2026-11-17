# syntax=docker/dockerfile:1.7
# CloudCostRadar Kubernetes agent: small, non-root, read-only root filesystem friendly.
ARG PYTHON_IMAGE=python:3.14.10-slim-trixie

FROM ${PYTHON_IMAGE} AS wheels
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /src
COPY agent/pyproject.toml ./
COPY agent/src ./src
RUN pip wheel --wheel-dir /wheels .

FROM ${PYTHON_IMAGE}
ARG VERSION=0.0.0-dev
LABEL org.opencontainers.image.title="CloudCostRadar agent" \
      org.opencontainers.image.vendor="Yugiverse" \
      org.opencontainers.image.licenses="LicenseRef-Proprietary" \
      org.opencontainers.image.version="${VERSION}"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
# The agent talks to the Kubernetes API with its own small read-only client (kube.py)
# instead of the official client library (about 127 MB), and installs from a bind
# mount so the wheels never become a layer of their own (11.19).
RUN --mount=type=bind,from=wheels,source=/wheels,target=/wheels \
    pip install --no-index --find-links=/wheels cloudcostradar-agent
COPY LICENSE THIRD_PARTY_NOTICES.md /opt/ccradar/
USER 10001:10001
ENTRYPOINT ["cloudcostradar-agent"]

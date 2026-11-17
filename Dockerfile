# syntax=docker/dockerfile:1.7
# One image for every deployment mode (local, SaaS, self-hosted).
# Stage 1 builds the web UI; stage 2 builds Python wheels; stage 3 is the runtime.

ARG NODE_IMAGE=node:26.13.0-trixie-slim
ARG PYTHON_IMAGE=python:3.14.10-slim-trixie

FROM ${NODE_IMAGE} AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json frontend/.npmrc ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM ${PYTHON_IMAGE} AS wheels
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /src
COPY backend/pyproject.toml ./
COPY backend/src ./src
RUN pip wheel --wheel-dir /wheels .

FROM ${PYTHON_IMAGE} AS runtime
ARG VERSION=0.0.0-dev
LABEL org.opencontainers.image.title="CloudCostRadar" \
      org.opencontainers.image.vendor="Yugiverse" \
      org.opencontainers.image.licenses="LicenseRef-Proprietary" \
      org.opencontainers.image.version="${VERSION}"
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CCRADAR_UI_DIST_DIR=/app/ui \
    CCRADAR_DATABASE_URL=sqlite:////data/ccradar.db \
    CCRADAR_DATA_DIR=/data
# PostgreSQL 17 client tools for `ccradar backup` / `ccradar restore` (D.8).
RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-client-17 \
 && rm -rf /var/lib/apt/lists/*
RUN groupadd --system --gid 10001 ccradar \
 && useradd --system --uid 10001 --gid ccradar --home-dir /app --shell /usr/sbin/nologin ccradar \
 && mkdir -p /data /app && chown ccradar:ccradar /data
# Install from the wheels stage without copying the wheels into a layer of their own
# (a later `rm` would not shrink the image). In the same layer, drop the parts of
# pyarrow that CloudCostRadar never uses (Arrow Flight, Substrait, the S3/Azure/GCS
# file systems, its own tests, C++ headers and Cython sources): about 70 MB (11.19).
# pyarrow loads those parts only on request, and the last line fails the build if
# reading and writing Parquet no longer works.
RUN --mount=type=bind,from=wheels,source=/wheels,target=/wheels \
    pip install --no-index --find-links=/wheels ccradar \
 && cd "$(python -c 'import os, pyarrow; print(os.path.dirname(pyarrow.__file__))')" \
 && rm -rf tests include src \
 && rm -f _flight.*.so libarrow_flight.so* libarrow_python_flight.so* \
          _substrait.*.so libarrow_substrait.so* \
          _s3fs.*.so libarrow_s3.so* _azurefs.*.so _gcsfs.*.so \
          _pyarrow_cpp_tests.*.so \
 && find . -name '*.pxd' -delete -o -name '*.pyx' -delete -o -name '*.pxi' -delete \
 && python -c "import io, pyarrow as pa, pyarrow.parquet as pq, pyarrow.dataset, pyarrow.fs; \
b = io.BytesIO(); pq.write_table(pa.table({'a': [1, 2]}), b); b.seek(0); \
assert pq.ParquetFile(b).read().num_rows == 2"
COPY --from=ui /ui/dist /app/ui
COPY LICENSE THIRD_PARTY_NOTICES.md /app/
WORKDIR /app
USER 10001:10001
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s \
  CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)" || exit 1
COPY --chmod=0755 deploy/docker/entrypoint.sh /usr/local/bin/ccradar-entrypoint
ENTRYPOINT ["ccradar-entrypoint"]
CMD ["web"]

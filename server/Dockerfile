# Mergeclear server. Build from the repository root (it bundles the shared
# contract code from scanner/):
#
#   docker build -f server/Dockerfile -t mergeclear/server .
#
# Behind a registry mirror:          --build-arg PYTHON_IMAGE=registry.internal/python:3.12-slim
# Behind a TLS-inspecting proxy:     --secret id=ca,src=corporate-ca.pem   (used for pip only, not kept in the image)
ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MERGECLEAR_DOCS_DIR=/data/docs \
    MERGECLEAR_DATABASE_DIR=/data/database

RUN useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin mergeclear

WORKDIR /app
COPY server/requirements.txt server/requirements.txt
RUN --mount=type=secret,id=ca,required=false \
    if [ -s /run/secrets/ca ]; then export PIP_CERT=/run/secrets/ca; fi; \
    pip install --no-cache-dir -r server/requirements.txt

# only the pure-Python contract module is used here, not the tree-sitter scanner
COPY scanner/setup.py scanner/README.md scanner/
COPY scanner/mergeclear scanner/mergeclear
RUN pip install --no-cache-dir --no-deps ./scanner && rm -rf scanner

COPY server/app server/app
RUN mkdir -p /data && chown mergeclear:mergeclear /data

USER mergeclear
VOLUME ["/data"]
WORKDIR /app/server
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

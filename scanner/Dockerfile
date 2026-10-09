# mergeclear CLI as a container, for build machines without Python:
#
#   docker build -t mergeclear/scanner scanner/
#   docker run --rm -u "$(id -u):$(id -g)" -v "$PWD:/repo" mergeclear/scanner check --base origin/main
#
# Behind a registry mirror:          --build-arg PYTHON_IMAGE=registry.internal/python:3.12-slim
# Behind a TLS-inspecting proxy:     --secret id=ca,src=corporate-ca.pem   (used at build time only)
ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}

# git is how the CLI learns commits, branches and changed files. Mounted repositories
# belong to another user, so mark every path safe for git. Packages come over HTTPS
# (works behind HTTPS-only egress proxies; apt verifies signatures either way).
# (Base images that already ship git skip the install.)
RUN --mount=type=secret,id=ca,required=false \
    set -e; \
    if ! command -v git >/dev/null; then \
        if [ -s /run/secrets/ca ]; then echo "Acquire::https::CAInfo \"/run/secrets/ca\";" > /etc/apt/apt.conf.d/99ca; fi; \
        sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/*.sources 2>/dev/null || true; \
        apt-get update; \
        apt-get install -y --no-install-recommends git; \
        rm -f /etc/apt/apt.conf.d/99ca; \
        rm -rf /var/lib/apt/lists/*; \
    fi; \
    git config --system --add safe.directory '*'

WORKDIR /src
COPY setup.py README.md ./
COPY mergeclear ./mergeclear
RUN --mount=type=secret,id=ca,required=false \
    if [ -s /run/secrets/ca ]; then export PIP_CERT=/run/secrets/ca; fi; \
    pip install --no-cache-dir . && rm -rf /src

# any uid can run it (-u), so keep HOME writable
ENV HOME=/tmp PYTHONDONTWRITEBYTECODE=1
WORKDIR /repo
ENTRYPOINT ["mergeclear"]
CMD ["--help"]

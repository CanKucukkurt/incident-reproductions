# Native (arm64 or amd64) rebuild of SWE-bench Pro task image
#   instance_gravitational__teleport-3587cca7840f636489449113969a5066025dd5bf
# following ScaleAI's base + instance Dockerfiles (golang:1.16-bullseye, clone teleport, checkout the base commit,
# `go mod download`, build teleport/tctl/tsh), plus the Codex CLI so `codex app-server` runs inside the container.
# Why not the published ghcr image: it is amd64-only and Codex's Linux sandbox (bubblewrap + seccomp) cannot install
# its seccomp filter under Rosetta emulation on Apple silicon; natively it works.
# Build: docker build --build-arg CODEX_VERSION=0.154.0 -t compaction-codex:teleport-3587cca-native sandbox/teleport-arm64
FROM golang:1.16-bullseye
ARG BASE_COMMIT=2308160e4e16359f8fd3ad24cd251b9cd80fae23
ARG CLONE_COMMIT=626ec2a48416b10a88641359a169d99e935ff037
ARG CODEX_VERSION=0.154.0
ARG NODE_MAJOR=20
# bullseye moved to archive.debian.org
RUN set -eux; sed -i 's|deb.debian.org/debian|archive.debian.org/debian|g; /bullseye-updates/d; /security/d' /etc/apt/sources.list; cat /etc/apt/sources.list; \
    echo 'Acquire::Check-Valid-Until "false";' > /etc/apt/apt.conf.d/99no-check-valid; \
    apt-get update; apt-get install -y --no-install-recommends git bash build-essential python3 python3-pip python3-setuptools python-is-python3 curl ca-certificates xz-utils; \
    apt-get clean; rm -rf /var/lib/apt/lists/*
RUN mkdir /app
WORKDIR /app
RUN git clone https://github.com/gravitational/teleport.git . && git checkout ${CLONE_COMMIT}
ENV PYTEST_ADDOPTS="--tb=short -v --continue-on-collection-errors --reruns=3"
ENV UV_HTTP_TIMEOUT=60
# instance preprocess: pin to the task's base commit and drop everything else (single ref, no future history)
RUN set -eux; git reset --hard ${BASE_COMMIT}; git clean -fdx; git checkout ${BASE_COMMIT}; \
    git checkout -B main; git remote remove origin; \
    git for-each-ref --format='%(refname)' | grep -v '^refs/heads/main$' | xargs -r -n1 git update-ref -d; \
    git reflog expire --expire=now --all; git gc --prune=now -q; \
    if git cat-file -e 3587cca7840f636489449113969a5066025dd5bf 2>/dev/null; then echo "future fix commit still present"; exit 1; fi; \
    git for-each-ref; du -sh .git
# Go's build cache must live somewhere the workspace-write sandbox can write (cwd or /tmp): warm it there.
ENV GOCACHE=/tmp/go-build-cache
RUN set -eux; go mod download; \
    export CGO_ENABLED=1 BUILDDIR=build OS=$(go env GOOS) ARCH=$(go env GOARCH) WEBASSETS_SKIP_BUILD=1 TELEPORT_DEBUG=yes; \
    mkdir -p build; (make -C /app build/teleport build/tctl build/tsh || go build ./...); go vet ./lib/backend >/dev/null 2>&1 || true; \
    git status --porcelain | head -5; git clean -fdx -q; git status --porcelain | wc -l
# Node + Codex CLI (pinned to the host's version); expose every bundled native binary directly (the npm JS shim
# breaks the sandbox helper re-exec).
RUN set -eux; arch="$(dpkg --print-architecture)"; case "$arch" in amd64) narch=x64;; arm64) narch=arm64;; *) echo "unsupported $arch"; exit 1;; esac; \
    ver="$(curl -fsSL https://nodejs.org/dist/latest-v${NODE_MAJOR}.x/SHASUMS256.txt | grep -o "node-v${NODE_MAJOR}\.[0-9]*\.[0-9]*-linux-${narch}\.tar\.xz" | head -1)"; \
    curl -fsSL "https://nodejs.org/dist/latest-v${NODE_MAJOR}.x/${ver}" -o /tmp/node.tar.xz; tar -xJf /tmp/node.tar.xz -C /usr/local --strip-components=1; rm /tmp/node.tar.xz; \
    npm install -g "@openai/codex@${CODEX_VERSION}"; npm cache clean --force; \
    bin="$(find /usr/local/lib/node_modules/@openai -type f -name codex -path '*vendor*/bin/*' | head -1)"; test -x "$bin"; \
    for b in "$(dirname "$bin")"/*; do ln -sf "$b" "/usr/local/bin/$(basename "$b")"; done; codex --version; \
    node --version; rm -f /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/corepack
WORKDIR /app
# Codex runs commands through `bash -lc`; Debian's /etc/profile resets PATH, which would hide the Go toolchain.
RUN printf 'export PATH=/go/bin:/usr/local/go/bin:$PATH\nexport GOPATH=/go GOCACHE=/tmp/go-build-cache\n' > /etc/profile.d/99-task-env.sh

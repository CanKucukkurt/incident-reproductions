# Derived agent image: a SWE-bench Pro v2 task image + the Codex CLI, so `codex app-server` can run INSIDE the
# task container and the agent's shell commands execute in the task's real environment (offline via Codex's sandbox).
# The Pro v2 images already ship Node (v22); only the CLI is added, pinned to the host's version.
# Build:  docker build --platform linux/amd64 --build-arg BASE_IMAGE=<task image> --build-arg CODEX_VERSION=0.154.0 \
#            -t compaction-codex:<tag> sandbox/codex-container
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
ARG CODEX_VERSION=0.154.0
RUN set -eux; node --version; npm install -g "@openai/codex@${CODEX_VERSION}"; npm cache clean --force; \
    # expose the native binary directly: the npm JS shim re-execs `codex` by name for its sandbox helper and breaks
    bin="$(find /usr/local/lib/node_modules/@openai -type f -name codex -path '*vendor*' | head -1)"; test -x "$bin"; \
    ln -sf "$bin" /usr/local/bin/codex; codex --version
# Go must be able to write its build cache under the workspace-write sandbox (only cwd and /tmp are writable).
ENV GOCACHE=/tmp/go-build-cache
WORKDIR /app

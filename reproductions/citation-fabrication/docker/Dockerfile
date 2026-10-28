FROM python:3.12-slim-bookworm AS service
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /opt/environment
COPY citation_environment /opt/environment/citation_environment
RUN mkdir -p /logs /data
CMD ["sleep", "infinity"]

FROM python:3.12-slim-bookworm AS agent
ENV PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash ca-certificates curl git procps \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir requests==2.32.5 \
    && useradd --create-home --uid 1000 agent \
    && mkdir -p /workspace /mnt/data \
    && chown agent:agent /workspace /mnt/data
USER agent
WORKDIR /workspace
CMD ["sleep", "infinity"]

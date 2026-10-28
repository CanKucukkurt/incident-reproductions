# Sandbox image for run_python. Build once:  docker build -t compaction-reader-sandbox:latest sandbox/
FROM python:3.11-slim
RUN pip install --no-cache-dir pandas==2.2.* openpyxl==3.1.* pypdf==4.* \
 && useradd -m -u 1000 runner
WORKDIR /work
USER 1000
CMD ["python", "-"]

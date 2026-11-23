# Use an official, lightweight Python base image
FROM python:3.14-slim AS base

# Set environment variables to optimize Python execution in containers
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=8080

# Create a non-root system user and group for security with UID/GID 1001
RUN groupadd -g 1001 appgroup && useradd -u 1001 -g appgroup -d /app -s /sbin/nologin appuser

# Set the working directory
WORKDIR /app

# ffmpeg (audio), curl, and git: installing an addon fetches one commit of its repository
# (core/addons/install.py)
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg curl git \
    && rm -rf /var/lib/apt/lists/*

# Install the pinned dependencies first to optimize Docker build caching
# (requirements.lock, generated from requirements.txt: docs/operations.md → Dependencies)
COPY requirements.lock .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir --require-hashes -r requirements.lock

# The test suite on the container's Python. The pre-push hook builds this stage when
# the Dockerfile or requirements change (.githooks/pre-push): a failure stops the deploy.
# Not part of the final image; `docker build .` skips it.
FROM base AS test
RUN apt-get update && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*
COPY . .
RUN python bin/test

FROM base AS app

# Copy application source code
COPY main.py .
COPY VERSION .
COPY core/ ./core/
# An addon's worker/ (a sidecar) is its own image, not part of this one.
COPY --exclude=*/worker addons/ ./addons/
COPY static/ ./static/
# Extra Python packages declared by official addons (addons/<name>/requirements.txt). Every other
# addon is installed into the data volume (<data dir>/addons, docs/addons.md) and uses these.
RUN set -e; for req in addons/*/requirements.txt; do \
        if [ -f "$req" ]; then pip install --no-cache-dir -r "$req"; fi; \
    done
# The administration CLI (users, addons; docs/access.md, "Managing users"), on PATH:
# docker compose exec dana dana users list, kubectl exec … dana addons workers --json
COPY bin/dana ./bin/dana
COPY cli/ ./cli/
# The generated addon reference: bin/dana addons new copies it into each new addon as REFERENCE.md
COPY docs/addon-reference.md ./docs/addon-reference.md
ENV PATH="/app/bin:${PATH}"

# Ensure all files are owned by the non-root user; /app/db is where the data volume goes and
# /backups the backups' (a new Docker volume takes this folder's owner; docs/backups.md)
RUN mkdir -p /app/db /backups && chown -R appuser:appgroup /app /backups

# Switch to the non-root user
USER appuser

# Expose the default port
EXPOSE 8080

# Start the application
CMD ["python", "main.py"]

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

# Install ffmpeg and curl
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

# Install standard dependencies first to optimize Docker build caching
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

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
COPY integrations/ ./integrations/
COPY static/ ./static/
# Plugin kinds (docs/plugins.md): bin/deploy copies the ones kept outside the repository here.
COPY plugins/ ./plugins/
ENV DANA_PLUGIN_DIRS=/app/plugins
# The user administration CLI (docs/access.md, "Managing users"): kubectl exec … python bin/users.py
COPY bin/users.py ./bin/users.py
# The release name bin/deploy builds ("<VERSION>-<commit>"): /api/health reports it and the
# dashboard's ?v= cache-buster uses it, so every deploy reaches cached clients.
ARG RELEASE=""
RUN if [ -n "$RELEASE" ]; then echo "$RELEASE" > VERSION; fi

# Ensure all files are owned by the non-root user; /app/db is where the data volume goes
# (a new Docker volume takes this folder's owner)
RUN mkdir -p /app/db && chown -R appuser:appgroup /app

# Switch to the non-root user
USER appuser

# Expose the default port
EXPOSE 8080

# Start the application
CMD ["python", "main.py"]

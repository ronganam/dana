# The site's own addons (docs/site.md): empty unless bin/deploy passes
# --build-context site_addons=<folder>, which replaces this stage.
FROM scratch AS site_addons

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
# An addon's worker/ (a sidecar) is its own image (bin/deploy), not part of this one.
COPY --exclude=*/worker addons/ ./addons/
COPY static/ ./static/
# Site addons (docs/writing-an-addon.md): the ones bin/deploy brings from your site; the loader reads
# /app/addons-site when it exists (core/addon_dirs.py).
COPY --from=site_addons --exclude=*/worker / ./addons-site/
# Extra Python packages declared by official or site addons (addons/<name>/requirements.txt).
RUN set -e; for req in addons/*/requirements.txt addons-site/*/requirements.txt; do \
        if [ -f "$req" ]; then pip install --no-cache-dir -r "$req"; fi; \
    done
# The administration CLI (users, addons; docs/access.md, "Managing users"), on PATH:
# docker compose exec dana dana users list, kubectl exec … dana addons install NAME (into the data volume)
COPY bin/dana ./bin/dana
COPY cli/ ./cli/
# The generated addon reference: bin/dana addons new copies it into each new addon as REFERENCE.md
COPY docs/addon-reference.md ./docs/addon-reference.md
ENV PATH="/app/bin:${PATH}"
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

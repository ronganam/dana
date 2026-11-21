# The bank-worker sidecar: Node + Debian's Chromium around israeli-bank-scrapers (README.md).
# Built and pushed by bin/deploy as <registry>/dana-bank-worker:<release>, next to Dana's image.
FROM node:22-slim

# Chromium from Debian (puppeteer's own download is skipped); tzdata for Asia/Jerusalem dates.
RUN apt-get update \
    && apt-get install -y --no-install-recommends chromium tzdata ca-certificates fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

ENV NODE_ENV=production \
    TZ=Asia/Jerusalem \
    HOME=/tmp \
    PUPPETEER_SKIP_DOWNLOAD=1 \
    CHROMIUM_PATH=/usr/bin/chromium \
    # A pod has no user namespaces for Chromium's sandbox; it only opens the two bank sites.
    BANK_WORKER_NO_SANDBOX=1

WORKDIR /worker
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts && npm cache clean --force
COPY src ./src

# The same user as Dana's container: both write /app/db/money on the shared volume.
USER 1001:1001
CMD ["node", "src/main.js"]

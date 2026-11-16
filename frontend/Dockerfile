# syntax=docker/dockerfile:1.7
# Frontend Next.js (output: standalone), cổng 3000.
#
#   docker build -t chatbot-frontend \
#     --build-arg NEXT_PUBLIC_FIREBASE_API_KEY=... (…7 biến NEXT_PUBLIC_*) frontend/
#
# Biến NEXT_PUBLIC_* được Next nhúng vào bundle trình duyệt LÚC BUILD → truyền bằng build-arg
# (cấu hình web Firebase vốn công khai, không phải bí mật). Biến server (RAG_BACKEND_URL,
# MONGODB_*) đọc LÚC CHẠY từ env của compose, không nằm trong image.

ARG NODE_IMAGE=node:20.20.2-bookworm-slim

# ── deps ────────────────────────────────────────────────────────────────────
FROM ${NODE_IMAGE} AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund

# ── build ───────────────────────────────────────────────────────────────────
FROM ${NODE_IMAGE} AS builder
WORKDIR /app
ARG NEXT_PUBLIC_FIREBASE_API_KEY=""
ARG NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN=""
ARG NEXT_PUBLIC_FIREBASE_PROJECT_ID=""
ARG NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET=""
ARG NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID=""
ARG NEXT_PUBLIC_FIREBASE_APP_ID=""
ARG NEXT_PUBLIC_OFFICE_DATA=""
ENV NEXT_TELEMETRY_DISABLED=1 \
    NEXT_PUBLIC_FIREBASE_API_KEY=${NEXT_PUBLIC_FIREBASE_API_KEY} \
    NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN=${NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN} \
    NEXT_PUBLIC_FIREBASE_PROJECT_ID=${NEXT_PUBLIC_FIREBASE_PROJECT_ID} \
    NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET=${NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET} \
    NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID=${NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID} \
    NEXT_PUBLIC_FIREBASE_APP_ID=${NEXT_PUBLIC_FIREBASE_APP_ID} \
    NEXT_PUBLIC_OFFICE_DATA=${NEXT_PUBLIC_OFFICE_DATA}
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

# ── runtime ─────────────────────────────────────────────────────────────────
FROM ${NODE_IMAGE} AS runner
ARG APP_UID=10001
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=3000
RUN groupadd --gid ${APP_UID} app \
 && useradd --uid ${APP_UID} --gid app --no-create-home --shell /usr/sbin/nologin app
WORKDIR /app
# File thuộc root (chỉ đọc với app); riêng .next/cache cần ghi được cho tối ưu ảnh.
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
RUN mkdir -p .next/cache && chown app:app .next/cache

USER app
EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD ["node", "-e", "fetch('http://127.0.0.1:3000/').then(r => process.exit(r.ok ? 0 : 1)).catch(() => process.exit(1))"]

CMD ["node", "server.js"]

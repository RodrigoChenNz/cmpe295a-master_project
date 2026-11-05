FROM node:22-alpine AS web
WORKDIR /src
COPY services/dashboard-web/package*.json ./
RUN npm ci
COPY services/dashboard-web ./
ENV VITE_API_URL="" VITE_WS_URL=""
RUN npm run build
FROM caddy:2-alpine@sha256:e70b164da81ef698b3ad19af799ea6086ce6d9e48ed4079eb9fe26f6fbf14ee5
COPY --from=web /src/dist /srv/web
COPY infra/deploy/Caddyfile /etc/caddy/Caddyfile

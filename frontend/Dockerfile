# The React app, built once and served by nginx, which also proxies /api and /files to
# the backend: one origin, so no CORS anywhere.

FROM node:22-alpine AS build
WORKDIR /app

# Lockfile first, so a source edit does not reinstall every package.
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund

COPY frontend/ ./
RUN npm run build


FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
# At start nginx writes conf.d/default.conf from this, substituting only ${BACKEND_*}.
COPY frontend/nginx.conf.template /etc/nginx/templates/default.conf.template
# "backend" is the Compose service name; on ECS both containers share a task, so 127.0.0.1.
ENV BACKEND_HOST=backend \
    NGINX_ENVSUBST_FILTER=^BACKEND_
EXPOSE 80

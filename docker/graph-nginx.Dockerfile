# Jackdaw Sentry Graph - Packaged Frontend / Nginx Image

FROM node:20-alpine@sha256:f598378b5240225e6beab68fa9f356db1fb8efe55173e6d4d8153113bb8f333c AS frontend-builder

WORKDIR /workspace/frontend/app

COPY frontend/app/package.json frontend/app/package-lock.json ./
RUN npm ci

COPY frontend/app/ ./
RUN npm run build

FROM nginx:alpine@sha256:e7257f1ef28ba17cf7c248cb8ccf6f0c6e0228ab9c315c152f9c203cd34cf6d1

COPY docker/nginx.graph.conf /etc/nginx/nginx.conf
COPY --from=frontend-builder /workspace/frontend/app/dist /usr/share/nginx/html/app/dist

# TenderExtractPro — frontend image (static build + nginx)
#
# Build:
#   docker build -t <dockerhub-user>/tender-extract-frontend:latest ./frontend
# Run:
#   docker run --rm -p 3000:80 <dockerhub-user>/tender-extract-frontend:latest
#
# nginx proxies /api/* to the `backend` service on the compose network, so the
# browser talks to a single origin and CORS is not involved in the demo.

FROM node:20-alpine AS build

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1/ || exit 1

# Poliscope web image: static build served by nginx, which also reverse
# proxies /api to the API container so the browser sees one origin -- the
# same reason the Vite dev server proxies /api (see vite.config.ts). Without
# this, EventSource (the SSE workspace stream) would need CORS that
# production does not otherwise use.
#
#   docker build -f apps/web/Dockerfile -t poliscope-web .
#
FROM node:20-alpine AS build
WORKDIR /app
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci
COPY apps/web ./
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY apps/web/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80

FROM node:22-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci --no-audit --no-fund
COPY . .
ARG VITE_MAPBOX_TOKEN=""
ARG VITE_WEBOTS_STREAM_URL="ws://localhost:1234"
ENV VITE_MAPBOX_TOKEN=$VITE_MAPBOX_TOKEN VITE_WEBOTS_STREAM_URL=$VITE_WEBOTS_STREAM_URL
RUN npm run build

FROM caddy:2-alpine
COPY --from=build /app/dist /srv
RUN printf ':80 {\n\troot * /srv\n\tencode gzip\n\ttry_files {path} /index.html\n\tfile_server\n\theader Cache-Control "public, max-age=3600"\n}\n' > /etc/caddy/Caddyfile
EXPOSE 80

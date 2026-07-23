FROM node:22-alpine AS build
ARG NPM_CONFIG_REGISTRY
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.29-alpine
ENV NGINX_ALLOWED_HOSTS="~^.*$"
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/templates/default.conf.template
EXPOSE 80

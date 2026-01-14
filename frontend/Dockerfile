FROM node:20-alpine AS builder

WORKDIR /app

COPY ui/react-app/package*.json ./

RUN npm ci

COPY ui/react-app/ .

ARG VITE_API_URL=http://localhost:8000
ARG VITE_CLERK_PUBLISHABLE_KEY
ENV VITE_API_URL=${VITE_API_URL}
ENV VITE_CLERK_PUBLISHABLE_KEY=${VITE_CLERK_PUBLISHABLE_KEY}

RUN npm run build

FROM nginx:alpine

COPY --from=builder /app/dist /usr/share/nginx/html

COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD wget --no-verbose --tries=1 --spider http://localhost:8080/ || exit 1

CMD ["nginx", "-g", "daemon off;"]

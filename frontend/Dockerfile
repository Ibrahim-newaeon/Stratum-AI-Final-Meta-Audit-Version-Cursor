# =============================================================================
# Stratum AI - Frontend Dockerfile
# =============================================================================

# Development stage
FROM node:25-alpine AS development

WORKDIR /app

# Copy package files
COPY package*.json ./

# Install dependencies (use legacy-peer-deps for React 19 compatibility)
RUN npm install --legacy-peer-deps

# Copy source code
COPY . .

# Expose Vite dev server port
EXPOSE 5173

# Start development server
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]

# =============================================================================
# Production build stage
# =============================================================================
FROM node:25-alpine AS build

WORKDIR /app

COPY package*.json ./
RUN npm ci --legacy-peer-deps

COPY . .
RUN npm run build

# Production stage (memory audit Feb 2026)
FROM nginx:alpine AS production

# Copy built assets
COPY --from=build /app/dist /usr/share/nginx/html
# nginx.conf is a template: the image's entrypoint renders ${API_UPSTREAM} into
# /etc/nginx/conf.d/default.conf at startup (docker-compose: api:8000,
# Railway: api.railway.internal:8000).
COPY nginx.conf /etc/nginx/templates/default.conf.template
ENV API_UPSTREAM=api:8000
# Makes the entrypoint export NGINX_LOCAL_RESOLVERS (from /etc/resolv.conf) for the template
ENV NGINX_ENTRYPOINT_LOCAL_RESOLVERS=true

# The official image's entrypoint renders the template into /etc/nginx/conf.d as
# root and the master process then drops worker privileges to the `nginx` user
# (see `user nginx;` in the base image's nginx.conf), so no USER switch here.
RUN rm -f /etc/nginx/conf.d/default.conf

EXPOSE 8080

# Healthcheck for container orchestration
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD wget -q --spider http://localhost:8080/health || exit 1

CMD ["nginx", "-g", "daemon off;"]

# Result: ~25MB container vs ~500MB with Vite dev server (per memory audit)

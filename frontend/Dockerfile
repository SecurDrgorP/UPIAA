# Stage 1: Base / Builder
FROM node:20-alpine AS builder

WORKDIR /app
COPY package*.json ./
RUN npm ci --legacy-peer-deps
COPY . .

# Stage 2: Development (used by docker-compose.override.yml)
FROM builder AS development
# We don't RUN ng serve here; docker-compose overrides it with a volume and command
EXPOSE 4200
CMD ["npx", "ng", "serve", "--host", "0.0.0.0", "--port", "4200", "--poll", "2000"]

# Stage 3: Production Build
FROM builder AS prod-builder
RUN npx ng build --configuration production

# Stage 4: Serve with Nginx (production)
FROM nginx:alpine AS production

# Copy custom nginx config
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Copy built app from builder
COPY --from=prod-builder /app/dist/UPIAA-frontend/browser /usr/share/nginx/html

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]

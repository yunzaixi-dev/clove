# Multi-stage Dockerfile for Clove

# Stage 1: Build frontend
FROM node:20-alpine AS frontend-builder

# Install pnpm
RUN corepack enable && corepack prepare pnpm@latest --activate

WORKDIR /app/front

# Copy frontend package files
COPY front/package.json front/pnpm-lock.yaml ./

# Install dependencies
RUN pnpm install --frozen-lockfile

# Copy frontend source
COPY front/ ./

# Build frontend
RUN pnpm run build

# Stage 2: Build Python application
FROM python:3.11-slim AS app

WORKDIR /app

# Copy Python requirements
COPY requirements.txt ./

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ ./app/

# Copy built frontend from previous stage
COPY --from=frontend-builder /app/front/dist ./app/static

# Create data directory
RUN mkdir -p /data

# Environment variables
ENV DATA_FOLDER=/data
ENV HOST=0.0.0.0
ENV PORT=${PORT:-5201}

# Expose port
EXPOSE ${PORT:-5201}

# Run the application
CMD ["python", "-m", "app.main"]

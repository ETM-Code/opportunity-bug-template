# Opportunity Radar - Dockerfile for Fly.io deployment
# Supports two modes:
#   1. Batch: runs task and exits (scheduled via GitHub Actions)
#   2. Web: FastAPI server with scale-to-zero (auto-stop when idle)

FROM python:3.11-slim

WORKDIR /app

# Install runtime dependencies for Playwright
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Playwright dependencies
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpango-1.0-0 \
    libcairo2 \
    libatspi2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Copy and install application
COPY pyproject.toml .
COPY src/ src/
COPY data/ data/

RUN pip install --no-cache-dir . \
    && pip install --no-cache-dir playwright playwright-stealth \
    && playwright install chromium \
    && playwright install-deps chromium

# Set environment
ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

# Default command - run web server
# For batch jobs: fly machine run ... -- python -m opportunity_radar.main run
CMD ["uvicorn", "opportunity_radar.web.app:app", "--host", "0.0.0.0", "--port", "8080"]

FROM python:3.12-slim

# Playwright system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
    libdrm2 libdbus-1-3 libxkbcommon0 libatspi2.0-0 libxcomposite1 \
    libxdamage1 libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 \
    libcairo2 libasound2 libwayland-client0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies first (cache layer)
COPY pyproject.toml .
RUN pip install --no-cache-dir . && \
    playwright install chromium --with-deps

# Copy source code
COPY src/ src/
COPY .env* ./

# Output directory
RUN mkdir -p output

# Non-root user for security
RUN useradd -m scraper
RUN chown -R scraper:scraper /app
USER scraper

EXPOSE 8000

# Memory limit for Chromium stability
ENV PLAYWRIGHT_BROWSERS_PATH=/home/scraper/.cache/ms-playwright

# Install browsers as scraper user
USER root
RUN su scraper -c "playwright install chromium"
USER scraper

CMD ["uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "8000"]

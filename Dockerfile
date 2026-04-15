FROM python:3.12-slim

WORKDIR /app

# Install Playwright system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    && rm -rf /var/lib/apt/lists/*

# Install Playwright + Chromium
RUN pip install --no-cache-dir playwright \
    && playwright install --with-deps chromium

# Copy scripts and server
COPY scripts/ /app/scripts/
COPY server.py /app/server.py

# Expose port
EXPOSE 8080

# Override with your domain: docker run -e SITE_URL=http://tweet.marcinszyda.com ...
ENV SITE_URL=http://localhost:8080

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1

CMD ["python", "server.py", "--host", "0.0.0.0", "--port", "8080"]

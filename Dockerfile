FROM python:3.12-slim-bookworm

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

COPY pyproject.toml README.md LICENSE ./
COPY beast_scraper ./beast_scraper

RUN apt-get update && apt-get install -y --no-install-recommends libatomic1 && \
    pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir . && \
    playwright install --with-deps chromium && \
    useradd --create-home --uid 1000 app && \
    mkdir -p /app/data && chown -R app:app /app /ms-playwright && \
    rm -rf /var/lib/apt/lists/*

USER app

ENV HOST=0.0.0.0
ENV PORT=8000

EXPOSE 8000

CMD ["beast-scraper"]

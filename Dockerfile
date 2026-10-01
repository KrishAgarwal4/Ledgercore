FROM python:3.11-slim

WORKDIR /app

# Install system dependencies (e.g. libpq for postgres if needed)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency specifications
COPY pyproject.toml .

# Install dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -e .

# Copy application files
COPY ledgercore/ ./ledgercore/
COPY db/ ./db/
COPY frontend/ ./frontend/
COPY concurrency_test.py .
COPY README.md .

EXPOSE 8000

CMD ["uvicorn", "ledgercore.api.app:app", "--host", "0.0.0.0", "--port", "8000"]

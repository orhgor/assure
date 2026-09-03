FROM python:3.11-slim

WORKDIR /app

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV PORT=8765

ARG ASSURE_BUILD_SHA=unknown
ENV ASSURE_BUILD_SHA=${ASSURE_BUILD_SHA}

COPY requirements.txt .

RUN apt-get update && apt-get install -y poppler-utils && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir --prefer-binary -r requirements.txt gunicorn flask-cors httpx pytest-asyncio asgiref

COPY . .

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8765') + '/api/health', timeout=3)"

CMD exec gunicorn --workers 1 --threads 8 --bind 0.0.0.0:${PORT:-8765} prompt_matrix.web:app

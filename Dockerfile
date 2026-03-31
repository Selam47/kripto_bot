FROM python:3.12-slim

# Ortam değişkenleri
ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    TZ=Europe/Istanbul \ 
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

WORKDIR /app

# Sistem bağımlılıkları (Tek katmanda temiz kurulum)
RUN apt-get update && apt-get install -y \
    tzdata \
    ca-certificates \
    chromium \
    chromium-driver \
    build-essential \
    gcc \
    --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Playwright için sistem Chromium ayarları
ENV PLAYWRIGHT_BROWSERS_PATH=/usr/bin
ENV PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium

# Bağımlılıkları kopyala ve kur
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# --- KRİTİK DÜZELTME BURASI ---
# Sadece .py dosyalarını değil, tüm proje klasör yapısını kopyalıyoruz
COPY . . 

# Gereksiz dosyaların (Docker içine gitmemesi gerekenler) elenmesi için 
# proje ana dizininde bir .dockerignore dosyan olmalı (aşağıda belirttim).

# Klasörleri oluştur ve izinleri ayarla
RUN mkdir -p logs charts data \
    && groupadd -r appuser \
    && useradd -r -g appuser appuser \
    && chown -R appuser:appuser /app

USER appuser

# Sağlık kontrolü (Veritabanı bağlantısını test eder)
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import sqlite3; sqlite3.connect('trading_bot.db').close()" || exit 1

# Botu başlat
CMD ["python", "main.py"]
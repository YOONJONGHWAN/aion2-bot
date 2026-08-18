FROM python:3.11-slim

WORKDIR /app

# 시스템 필수 패키지 설치
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
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
    libpango-1.0-0 \
    libcairo2 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# [핵심] 브라우저 설치 경로를 고정하고 시스템 전체 권한 부여
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN playwright install chromium

COPY . .

CMD ["python", "Aion2_Update_Bot.py"]

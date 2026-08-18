FROM python:3.11-slim

WORKDIR /app

# 1. 필수 시스템 패키지 설치
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
    libasound2 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 2. 플레이라이트 환경 변수 및 설치 강제화
# 브라우저를 /app/ms-playwright에 설치하여 경로 문제 차단
ENV PLAYWRIGHT_BROWSERS_PATH=/app/ms-playwright
RUN playwright install chromium

COPY . .

CMD ["python", "Aion2_Update_Bot.py"]

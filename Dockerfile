FROM python:3.11-slim

WORKDIR /app

# 시스템 필수 패키지 및 브라우저 의존성 한 번에 설치
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

# 파이썬 라이브러리 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright 기본 경로에 크롬 브라우저와 종속성 강제 설치
RUN playwright install chromium
RUN playwright install-deps chromium

COPY . .

# 봇 실행
CMD ["python", "Aion2_Update_Bot.py"]

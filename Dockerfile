FROM python:3.11-slim

WORKDIR /app

# 시스템 필수 패키지 설치
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 파이썬 라이브러리 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# [중요] Playwright 브라우저 위치를 /app/ms-playwright로 강제 고정
ENV PLAYWRIGHT_BROWSERS_PATH=/app/ms-playwright

# 고정된 위치에 브라우저 설치
RUN playwright install --with-deps chromium

COPY . .

# 봇 실행
CMD ["python", "Aion2_Update_Bot.py"]

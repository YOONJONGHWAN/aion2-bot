# 1. 가장 안정적인 파이썬 공식 슬림 이미지 사용
FROM python:3.11-slim

# 2. 작업 디렉토리 설정
WORKDIR /app

# 3. 시스템 필수 패키지 설치
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 4. 파이썬 라이브러리 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 5. Playwright 브라우저 및 시스템 의존성 완벽 설치 (도커 빌드 안이라 권한 문제 없음)
RUN playwright install --with-deps chromium

# 6. 소스 코드 복사
COPY . .

# 7. 봇 실행
CMD ["python", "Aion2_Update_Bot.py"]

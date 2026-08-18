# 1. 브라우저가 미리 설치된 환경을 가져옵니다.
FROM mcr.microsoft.com/playwright/python:v1.62.0-noble

# 2. 작업할 폴더를 설정합니다.
WORKDIR /app

# 3. 내 컴퓨터에 있는 requirements.txt를 이 세팅된 컴퓨터로 복사해서 라이브러리를 설치합니다.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. 브라우저(크롬)를 설치합니다.
RUN playwright install chromium

# 5. 내 파이썬 코드들을 전부 이 컴퓨터로 복사합니다.
COPY . .

# 6. 마지막으로 내 봇을 실행합니다.
CMD ["python", "Aion2_Update_Bot.py"]
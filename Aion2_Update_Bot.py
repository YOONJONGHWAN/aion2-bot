import os
import asyncio
from flask import Flask
from threading import Thread
import discord
from discord.ext import tasks, commands
import requests
from bs4 import BeautifulSoup

# Flask 웹 서버 (Render 핑 유지용)
app = Flask('')

@app.route('/')
def home():
    return "Aion2 API Bot is running!"

def run_flask():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run_flask)
    t.start()

# 디스코드 봇 설정
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# 공지사항을 직접 HTML 파싱 또는 API로 가져오는 함수
def fetch_aion2_notices():
    notices = []
    try:
        # 실제 브라우저처럼 보이게 헤더 장착
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept-Language": "ko-KR,ko;q=0.9",
            "Referer": "https://aion2.plaync.com/"
        }
        
        url = "https://aion2.plaync.com/ko-kr/board/notice/list"
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # 페이지 내의 모든 a 태그 중 공지사항 링크 패턴 탐색
            for a in soup.find_all('a', href=True):
                href = a['href']
                title = a.get_text().strip()
                
                # 공지 상세 페이지 링크 조건 필터링
                if ('/board/notice/view' in href or 'view' in href) and len(title) > 2:
                    if not href.startswith('http'):
                        href = 'https://aion2.plaync.com' + href
                    
                    if not any(n['link'] == href for n in notices):
                        notices.append({'title': title, 'link': href})
                        
        notices = notices[:5]
    except Exception as e:
        print(f"데이터 수신 에러: {e}")
        
    return notices

# 디스코드 봇 준비 완료 이벤트
@bot.event
async def on_ready():
    print(f'로그인 완료: {bot.user.name} (ID: {bot.user.id})')
    print('----------------------------------------')
    if not check_aion2_updates.is_running():
        check_aion2_updates.start()

# 수동 확인 명령어 (!확인)
@bot.command(name='확인')
async def manual_check(ctx):
    await ctx.send("🔍 아이온2 최신 공지사항을 확인하는 중입니다...")
    
    # requests는 동기 방식이므로 비동기 스레드로 실행하거나 바로 호출
    notices = await asyncio.to_thread(fetch_aion2_notices)
    
    if not notices:
        await ctx.send("❌ 공지사항을 불러오지 못했거나 가져올 수 있는 항목이 없습니다.")
        return
        
    msg = "📢 **[아이온2 최신 공지사항]**\n"
    for idx, notice in enumerate(notices[:3], 1):
        msg += f"{idx}. [{notice['title']}]({notice['link']})\n"
        
    await ctx.send(msg)

# 5분 주기로 자동 확인
@tasks.loop(minutes=5)
async def check_aion2_updates():
    print("자동 공지 확인 중 (5분 주기)...")
    await asyncio.to_thread(fetch_aion2_notices)

# 봇 실행
if __name__ == "__main__":
    TOKEN = os.environ.get("DISCORD_TOKEN")
    if not TOKEN:
        print("Error: DISCORD_TOKEN 환경 변수가 설정되지 않았습니다.")
    else:
        keep_alive()
        bot.run(TOKEN)

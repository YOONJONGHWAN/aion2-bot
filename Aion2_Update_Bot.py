import os
import asyncio
from flask import Flask
from threading import Thread
import discord
from discord.ext import tasks, commands
import requests

# Flask 웹 서버 (Render 핑 유지용)
app = Flask('')

@app.route('/')
def home():
    return "Aion2 Update Bot is running!"

def run_flask():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run_flask)
    t.start()

# 디스코드 봇 설정
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# PlayNC 아이온2 공지사항 API를 직접 호출하여 데이터를 가져오는 함수
def get_latest_official_notices_via_api():
    notices = []
    try:
        # PlayNC 공지사항 게시판 목록 API 주소 (일반적으로 사용되는 공식 엔드포인트 패턴)
        api_url = "https://aion2.plaync.com/ko-kr/board/notice/list"
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://aion2.plaync.com/ko-kr/board/notice/list"
        }
        
        # 브라우저 우회가 어려울 경우 requests를 통해 HTML 파싱을 시도하거나 API를 호출합니다.
        # 만약 API 구조가 다르다면 BeautifulSoup을 활용한 백업 파싱을 시도합니다.
        from bs4 import BeautifulSoup
        response = requests.get(api_url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # 페이지 내에서 a 태그 중 게시글 링크 패턴을 모두 찾습니다.
            for a in soup.find_all('a', href=True):
                href = a['href']
                title = a.get_text(strip=True)
                
                if 'view' in href and len(title) > 2:
                    if not href.startswith('http'):
                        href = 'https://aion2.plaync.com' + href
                    
                    if not any(n['link'] == href for n in notices):
                        notices.append({'title': title, 'link': href})
                        
        notices = notices[:5]
    except Exception as e:
        print(f"API/크롤링 중 에러 발생: {e}")
        
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
    
    # 동기 함수이므로 루프를 통해 실행
    notices = await asyncio.to_thread(get_latest_official_notices_via_api)
    
    if not notices:
        await ctx.send("❌ 공지사항을 불러오지 못했거나 가져올 수 있는 항목이 없습니다.")
        return
        
    msg = "📢 **[아이온2 최신 공지사항]**\n"
    for idx, notice in enumerate(notices[:3], 1):
        msg += f"{idx}. [{notice['title']}]({notice['link']})\n"
        
    await ctx.send(msg)

# 일정 주기마다 자동으로 공지 확인 (예: 30분 마다)
@tasks.loop(minutes=30)
async def check_aion2_updates():
    print("자동 공지 확인 중...")
    notices = await asyncio.to_thread(get_latest_official_notices_via_api)
    if notices:
        pass

# 봇 실행
if __name__ == "__main__":
    TOKEN = os.environ.get("DISCORD_TOKEN")
    if not TOKEN:
        print("Error: DISCORD_TOKEN 환경 변수가 설정되지 않았습니다.")
    else:
        keep_alive()
        bot.run(TOKEN)

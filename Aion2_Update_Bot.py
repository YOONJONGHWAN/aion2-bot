import os
import asyncio
from flask import Flask
from threading import Thread
import discord
from discord.ext import tasks, commands
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

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

# 공지사항을 크롤링하는 비동기 함수 (Playwright 사용)
async def get_latest_official_notices_with_browser():
    notices = []
    try:
        async with async_playwright() as p:
            # 봇 감지를 피하기 위한 인자 추가
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-blink-features=AutomationControlled', # 자동화 감지 우회
                    '--disable-infobars',
                    '--window-size=1920,1080'
                ]
            )
            
            # 실제 사용자인 것처럼 유저 아이언(User-Agent) 설정
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 1080}
            )
            
            page = await context.new_page()
            
            target_url = "https://aion2.plaync.com/ko-kr/board/notice/list" 
            await page.goto(target_url, timeout=60000)
            
            # 충분한 로딩 대기 시간 부여
            await page.wait_for_timeout(5000)
            
            # 브라우저 내부에서 데이터 수집
            notices = await page.evaluate('''() => {
                const elements = document.querySelectorAll('a.title');
                const results = [];
                elements.forEach(el => {
                    let title = el.innerText.trim();
                    let link = el.getAttribute('href');
                    if (title) {
                        if (link && !link.startsWith('http')) {
                            link = 'https://aion2.plaync.com' + link;
                        }
                        results.push({ title: title, link: link });
                    }
                });
                return results;
            }''')
            
            await browser.close()
            notices = notices[:5]  # 상위 5개만 자르기
                    
    except Exception as e:
        print(f"크롤링 중 에러 발생: {e}")
        
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
    notices = await get_latest_official_notices_with_browser()
    
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
    notices = await get_latest_official_notices_with_browser()
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

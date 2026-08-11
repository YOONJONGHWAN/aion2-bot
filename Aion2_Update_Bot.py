import os
import asyncio
from flask import Flask
from threading import Thread
import discord
from discord.ext import tasks, commands
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

# Playwright를 이용해 실제 브라우저로 아이온2 공지사항 크롤링
async def get_latest_official_notices_via_playwright():
    notices = []
    async with async_playwright() as p:
        # 헤드리스 크롬 브라우저 실행
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"]
        )
        page = await browser.new_page(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        
        try:
            await page.goto("https://aion2.plaync.com/ko-kr/board/notice/list", timeout=30000)
            # 공지사항 리스트가 렌더링될 때까지 대기
            await page.wait_for_selector("a", timeout=10000)
            
            # 페이지 내 모든 링크 요소 수집
            links = await page.query_selector_all("a")
            for link in links:
                href = await link.get_attribute("href")
                title = await link.inner_text()
                title = title.strip()
                
                if href and 'view' in href and len(title) > 2:
                    if not href.startswith('http'):
                        href = 'https://aion2.plaync.com' + href
                    
                    if not any(n['link'] == href for n in notices):
                        notices.append({'title': title, 'link': href})
                        
            notices = notices[:5]
        except Exception as e:
            print(f"Playwright 크롤링 에러 발생: {e}")
        finally:
            await browser.close()
            
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
    
    notices = await get_latest_official_notices_via_playwright()
    
    if not notices:
        await ctx.send("❌ 공지사항을 불러오지 못했거나 가져올 수 있는 항목이 없습니다.")
        return
        
    msg = "📢 **[아이온2 최신 공지사항]**\n"
    for idx, notice in enumerate(notices[:3], 1):
        msg += f"{idx}. [{notice['title']}]({notice['link']})\n"
        
    await ctx.send(msg)

# 5분 주기로 자동으로 공지 확인
@tasks.loop(minutes=5)
async def check_aion2_updates():
    print("자동 공지 확인 중 (5분 주기)...")
    notices = await get_latest_official_notices_via_playwright()
    if notices:
        # 추후 자동 알림 기능 고도화 시 활용할 수 있는 영역입니다.
        pass

# 봇 실행
if __name__ == "__main__":
    TOKEN = os.environ.get("DISCORD_TOKEN")
    if not TOKEN:
        print("Error: DISCORD_TOKEN 환경 변수가 설정되지 않았습니다.")
    else:
        keep_alive()
        bot.run(TOKEN)

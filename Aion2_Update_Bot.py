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
            browser = await p.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage']
            )
            page = await browser.new_page()
            
            target_url = "https://aion2.plaync.com/ko-kr/board/notice/list" 
            await page.goto(target_url, timeout=60000)
            await page.wait_for_timeout(3000)
            
            content = await page.content()
            await browser.close()
            
            soup = BeautifulSoup(content, 'html.parser')
            items = soup.select('.board-list-item, .notice-item, li')
            
            for item in items[:5]:
                title_elem = item.select_one('.title, a')
                if title_elem:
                    title = title_elem.get_text(strip=True)
                    link = title_elem.get('href', '')
                    if link and not link.startswith('http'):
                        link = "https://aion2.plaync.com" + link
                    notices.append({"title": title, "link": link})
                    
    except Exception as e:
        print(f"크롤링 중 에러 발생: {e}")
        
    return notices

@bot.event
async def on_ready():
    print(f'로그인 완료: {bot.user.name} (ID: {bot.user.id})')
    print('----------------------------------------')
    if not check_aion2_updates.is_running():
        check_aion2_updates.start()

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

@tasks.loop(minutes=30)
async def check_aion2_updates():
    print("자동 공지 확인 중...")
    notices = await get_latest_official_notices_with_browser()
    if notices:
        pass

if __name__ == "__main__":
    # ⚠️ 원래 사용하시던 DISCORD_TOKEN 환경 변수 이름으로 원복했습니다.
    TOKEN = os.environ.get("DISCORD_TOKEN")
    if not TOKEN:
        print("Error: DISCORD_TOKEN 환경 변수가 설정되지 않았습니다.")
    else:
        keep_alive()
        bot.run(TOKEN)

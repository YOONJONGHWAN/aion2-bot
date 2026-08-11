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
    return "Aion2 Bot is running!"

def run_flask():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    Thread(target=run_flask, daemon=True).start()

# 디스코드 봇 설정
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

TARGET_URL = "https://aion2.plaync.com/ko-kr/board/cm_story/list"
last_seen_link = None  # 중복 알림 방지용 저장 변수

# Playwright로 cm_story 최신 글 가져오는 함수
async def fetch_latest_notices():
    notices = []
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            
            await page.goto(TARGET_URL, wait_until="networkidle", timeout=20000)
            await page.wait_for_timeout(2500)
            
            links = await page.locator('a[href*="/board/cm_story/view"]').all()
            seen = set()
            
            for link in links:
                title = (await link.inner_text()).strip()
                href = await link.get_attribute("href")
                
                if href and href not in seen and len(title) > 1:
                    seen.add(href)
                    if not href.startswith("http"):
                        href = "https://aion2.plaync.com" + href
                    notices.append({"title": title, "link": href})
                    if len(notices) >= 5:
                        break
                        
            await browser.close()
    except Exception as e:
        print(f"크롤링 에러 발생: {e}")
        
    return notices

# 봇 준비 완료 이벤트
@bot.event
async def on_ready():
    global last_seen_link
    print(f'로그인 완료: {bot.user.name}')
    
    # 처음 봇이 켜질 때 기준 최신 글 설정 (이전 글 폭풍 알림 방지)
    initial_notices = await fetch_latest_notices()
    if initial_notices:
        last_seen_link = initial_notices[0]['link']
        print(f"기준 최신 글 설정 완료: {initial_notices[0]['title']}")
        
    if not check_updates.is_running():
        check_updates.start()

# 수동 확인 명령어 (!확인)
@bot.command(name='확인')
async def manual_check(ctx):
    await ctx.send("🔍 CM 스토리 최신 게시글을 확인하는 중입니다...")
    notices = await fetch_latest_notices()
    
    if not notices:
        await ctx.send("❌ 게시글을 불러오지 못했습니다.")
        return
        
    embed = discord.Embed(
        title="📢 아이온2 CM 스토리 최신 소식",
        color=discord.Color.blue()
    )
    for idx, notice in enumerate(notices[:3], 1):
        embed.add_field(
            name=f"{idx}. {notice['title']}",
            value=f"[게시글 바로가기]({notice['link']})",
            inline=False
        )
        
    await ctx.send(embed=embed)

# 30분 주기로 새 글 확인 및 자동 알림
@tasks.loop(minutes=30)
async def check_updates():
    global last_seen_link
    notices = await fetch_latest_notices()
    
    if not notices:
        return
        
    latest = notices[0]
    
    # 새 글이 등록되었을 경우
    if last_seen_link and latest['link'] != last_seen_link:
        last_seen_link = latest['link']
        
        # DISCORD_CHANNEL_ID 환경 변수가 설정된 채널로 자동 알림 전송
        channel_id = os.environ.get("DISCORD_CHANNEL_ID")
        if channel_id:
            channel = bot.get_channel(int(channel_id))
            if channel:
                embed = discord.Embed(
                    title="🆕 아이온2 새 소식이 등록되었습니다!",
                    description=f"**[{latest['title']}]({latest['link']})**",
                    color=discord.Color.green()
                )
                await channel.send(embed=embed)

if __name__ == "__main__":
    TOKEN = os.environ.get("DISCORD_TOKEN")
    if TOKEN:
        keep_alive()
        bot.run(TOKEN)
    else:
        print("Error: DISCORD_TOKEN 환경 변수를 찾을 수 없습니다.")

import os
import asyncio
from threading import Thread
from flask import Flask
import discord
from discord.ext import commands, tasks
from playwright.async_api import async_playwright

# --------------------------------------------------
# 1. Render 웹 서버 유지용 Flask 설정
# --------------------------------------------------
app = Flask('')

@app.route('/')
def home():
    return "AION2 Bot is running!"

def run_flask():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()

# --------------------------------------------------
# 2. 디스코드 봇 및 기본 변수 설정
# --------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

TOKEN = os.getenv('DISCORD_TOKEN')
TARGET_URL = "https://aion2.plaync.com/ko-kr/board/cmstory/list"

# ★ 본인의 디스코드 채널 ID(숫자)로 변경
NOTIFICATION_CHANNEL_ID = 123456789012345678  

last_post_link = None  # 중복 감지용 변수

# --------------------------------------------------
# 3. 기존 동작하던 Playwright 크롤링 함수 (구조 원복)
# --------------------------------------------------
async def fetch_latest_posts(limit=3):
    async with async_playwright() as p:
        browser_args = [
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu"
        ]
        
        try:
            browser = await p.chromium.launch(headless=True, args=browser_args)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/123.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 720}
            )
            page = await context.new_page()
            
            await page.goto(TARGET_URL, timeout=60000, wait_until="domcontentloaded")
            await page.wait_for_selector('a.title', timeout=15000)
            
            title_elements = await page.query_selector_all('a.title')
            posts = []
            
            for elem in title_elements[:limit]:
                title = await elem.inner_text()
                link = await elem.get_attribute('href')
                
                if link and link.startswith('/'):
                    link = "https://aion2.plaync.com" + link
                    
                posts.append({"title": title.strip(), "link": link})
                
            await browser.close()
            return posts

        except Exception as e:
            print(f"[ERROR] 크롤링 실패: {e}")
            return []

# --------------------------------------------------
# 4. 봇 준비 이벤트 및 5분 주기 자동 알림 (중복 제거 적용)
# --------------------------------------------------
@bot.event
async def on_ready():
    print(f"[INFO] 디스코드 봇 로그인 완료: {bot.user.name}")
    if not auto_check_update.is_running():
        auto_check_update.start()

@tasks.loop(minutes=5)
async def auto_check_update():
    global last_post_link
    
    channel = bot.get_channel(NOTIFICATION_CHANNEL_ID)
    if not channel:
        return

    posts = await fetch_latest_posts(limit=3)
    if not posts:
        return

    # 최초 실행 시 가장 최근 글을 기준점으로 등록만 함
    if last_post_link is None:
        last_post_link = posts[0]['link']
        print(f"[INFO] 최초 기준점 설정: {last_post_link}")
        return

    # 기존 최신글 이전까지의 '새 게시글'만 수집
    new_posts = []
    for post in posts:
        if post['link'] == last_post_link:
            break
        new_posts.append(post)

    # 새 글이 있을 때만 알림 전송 후 기준점 업데이트
    if new_posts:
        last_post_link = new_posts[0]['link']
        msg = f"🎉 **새로운 게시글이 등록되었습니다! ({len(new_posts)}개)**\n\n"
        for post in reversed(new_posts):
            msg += f"📢 **{post['title']}**\n🔗 {post['link']}\n\n"
        await channel.send(msg)

# --------------------------------------------------
# 5. 수동 명령어 (!확인)
# --------------------------------------------------
@bot.command(name='확인')
async def check_update(ctx):
    await ctx.send("CM 스토리 최신 게시글을 확인하는 중입니다...")
    posts = await fetch_latest_posts(limit=3)
    
    if posts:
        msg = "📢 **현재 게시판 최신글 목록:**\n\n"
        for idx, post in enumerate(posts, 1):
            msg += f"**{idx}. {post['title']}**\n🔗 {post['link']}\n\n"
        await ctx.send(msg)
    else:
        await ctx.send("게시글을 불러오지 못했습니다.")

# --------------------------------------------------
# 6. 실행
# --------------------------------------------------
if __name__ == "__main__":
    keep_alive()
    bot.run(TOKEN)

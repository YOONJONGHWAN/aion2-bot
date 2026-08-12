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
# 2. 디스코드 봇 및 환경 변수 설정
# --------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

TOKEN = os.getenv('DISCORD_TOKEN')

# Render Environment에 등록된 NOTIFICATION_CHANNEL_ID 읽어오기
CHANNEL_ID_ENV = os.getenv('NOTIFICATION_CHANNEL_ID')
NOTIFICATION_CHANNEL_ID = int(CHANNEL_ID_ENV) if CHANNEL_ID_ENV and CHANNEL_ID_ENV.isdigit() else None

TARGET_URL = "https://aion2.plaync.com/ko-kr/board/cmstory/list"

last_seen_link = None  # 중복 감지용 마지막 알림 게시글 링크

# --------------------------------------------------
# 3. Playwright 크롤링 함수 (user_agent 복원)
# --------------------------------------------------
async def fetch_latest_posts(limit=5):
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True, args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu"
            ])
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
# 4. 자동 감지 로직 (다중 새 글 순서대로 전송 & 중복 방지)
# --------------------------------------------------
@bot.event
async def on_ready():
    print(f"[INFO] 디스코드 봇 로그인 완료: {bot.user.name}")
    if NOTIFICATION_CHANNEL_ID:
        print(f"[INFO] 알림 대상 채널 ID: {NOTIFICATION_CHANNEL_ID}")
    else:
        print("[WARN] NOTIFICATION_CHANNEL_ID 환경 변수가 설정되지 않았거나 올바르지 않습니다.")
        
    if not auto_check_update.is_running():
        auto_check_update.start()

@tasks.loop(minutes=5)
async def auto_check_update():
    global last_seen_link
    
    if not NOTIFICATION_CHANNEL_ID:
        return

    channel = bot.get_channel(NOTIFICATION_CHANNEL_ID)
    if not channel:
        return

    posts = await fetch_latest_posts(limit=5)
    if not posts:
        return

    # 처음 실행 시 최신글 1개를 기준점으로 저장
    if last_seen_link is None:
        last_seen_link = posts[0]['link']
        print(f"[INFO] 최초 기준점 저장: {last_seen_link}")
        return

    # 이전에 전송했던 글(last_seen_link) 이전까지의 모든 '새 글'만 수집
    new_posts = []
    for post in posts:
        if post['link'] == last_seen_link:
            break
        new_posts.append(post)

    # 새 글이 있으면 기준점 업데이트 후 오래된 글부터 순서대로 전송
    if new_posts:
        last_seen_link = new_posts[0]['link']
        for post in reversed(new_posts):
            await channel.send(f"📢 **[새 게시글] {post['title']}**\n🔗 {post['link']}")

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

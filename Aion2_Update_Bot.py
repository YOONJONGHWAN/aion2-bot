import os
import asyncio
import subprocess
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
TARGET_URL = "https://aion2.plaync.com/ko-kr/board/cmstory/list"  # 아이온2 CM 스토리 URL

# ★ 본인의 디스코드 채널 ID(숫자)로 변경해주세요 (채널 우클릭 -> 채널 ID 복사)
NOTIFICATION_CHANNEL_ID = 123456789012345678  

last_post_link = None  # 중복 감지용 마지막 글 링크 저장 변수

# --------------------------------------------------
# 3. Playwright 크롤링 함수 (최신글 N개 가져오기)
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
        except Exception as launch_err:
            if "Executable doesn't exist" in str(launch_err):
                subprocess.run(["playwright", "install", "chromium"])
                browser = await p.chromium.launch(headless=True, args=browser_args)
            else:
                raise launch_err
        
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/123.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 720}
        )
        page = await context.new_page()
        posts = []
        
        try:
            await page.goto(TARGET_URL, timeout=60000, wait_until="domcontentloaded")
            await page.wait_for_selector('a.title', timeout=15000)
            
            title_elements = await page.query_selector_all('a.title')
            for elem in title_elements[:limit]:
                title = await elem.inner_text()
                link = await elem.get_attribute('href')
                
                if link and link.startswith('/'):
                    link = "https://aion2.plaync.com" + link
                    
                posts.append({"title": title.strip(), "link": link})
                
            return posts

        except Exception as e:
            print(f"[ERROR] 크롤링 실패: {e}")
            return []
        finally:
            await browser.close()

# --------------------------------------------------
# 4. 봇 준비 이벤트 및 5분 주기 자동 알림 루프
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
        print("[WARN] 알림 채널을 찾을 수 없습니다. NOTIFICATION_CHANNEL_ID를 확인하세요.")
        return

    posts = await fetch_latest_posts(limit=3)
    if not posts:
        return

    # 봇 시작 시 최초 1회 기준점 설정
    if last_post_link is None:
        last_post_link = posts[0]['link']
        print(f"[INFO] 최초 기준점 설정 완료: {last_post_link}")
        return

    # 새 글만 선별
    new_posts = []
    for post in posts:
        if post['link'] == last_post_link:
            break
        new_posts.append(post)

    # 새 글이 존재할 때만 알림 전송 (없으면 무시)
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
# 6. 실행 구문
# --------------------------------------------------
if __name__ == "__main__":
    keep_alive()
    bot.run(TOKEN)

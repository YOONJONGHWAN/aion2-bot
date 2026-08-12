import os
import asyncio
from threading import Thread
from flask import Flask
import discord
from discord.ext import commands, tasks
from playwright.async_api import async_playwright

# 1. Render 웹 서버 유지용 Flask 설정
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

# 2. 디스코드 봇 및 설정
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

TOKEN = os.getenv('DISCORD_TOKEN')
TARGET_URL = "https://aion2.plaync.com/ko-kr/board/cmstory/list"

# ★ 본인 디스코드 채널 ID 입력
NOTIFICATION_CHANNEL_ID = 123456789012345678  

# 가장 최근에 알림을 보낸 게시글 링크 저장 변수
last_seen_link = None  

# 3. 기존 동작하던 크롤링 함수 (상위 5개 수집으로 확대)
async def fetch_latest_posts(limit=5):
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True, args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu"
            ])
            page = await browser.new_page()
            
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
            print(f"[ERROR] 크롤링 중 예외 발생: {e}")
            return []

# 4. 자동 감지 로직 (하루에 여러 개 올라올 때 누락 방지 처리)
@bot.event
async def on_ready():
    print(f"[INFO] 로그인 완료: {bot.user.name}")
    if not auto_check_update.is_running():
        auto_check_update.start()

@tasks.loop(minutes=5)
async def auto_check_update():
    global last_seen_link
    
    channel = bot.get_channel(NOTIFICATION_CHANNEL_ID)
    if not channel:
        return

    # 하루에 2~3개 이상 올라오는 상황을 고려해 상위 5개를 가져옴
    posts = await fetch_latest_posts(limit=5)
    if not posts:
        return

    # 봇이 처음 켜졌을 때는 현재 최신글 1개만 기준점으로 등록
    if last_seen_link is None:
        last_seen_link = posts[0]['link']
        print(f"[INFO] 최초 기준점 저장: {last_seen_link}")
        return

    # 가져온 상위 5개 글 중, 기존에 보았던 글(last_seen_link)이 나오기 직전까지를 전부 '새 글'로 수집
    new_posts = []
    for post in posts:
        if post['link'] == last_seen_link:
            break
        new_posts.append(post)

    # 새 글이 존재할 경우
    if new_posts:
        # 가장 최근 글 링크로 기준점 업데이트
        last_seen_link = new_posts[0]['link']
        
        # 여러 개가 한 번에 올라왔다면 작성된 순서(오래된 글 -> 최신 글)대로 각각 알림 전송
        for post in reversed(new_posts):
            await channel.send(f"📢 **[새 게시글] {post['title']}**\n🔗 {post['link']}")

# 5. 수동 확인 명령어
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

# 6. 실행
if __name__ == "__main__":
    keep_alive()
    bot.run(TOKEN)

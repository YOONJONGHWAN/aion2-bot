import os
import asyncio
import traceback
from threading import Thread
from flask import Flask
import aiohttp
import discord
from discord.ext import commands, tasks

# --------------------------------------------------
# 1. Render 웹 서버 유지용 Flask 설정 (포트 자동 감지)
# --------------------------------------------------
app = Flask('')

@app.route('/')
def home():
    return "AION2 Bot is running!"

def run_flask():
    # Render가 부여하는 동적 PORT 환경변수를 읽고, 없으면 8080 기본값 사용
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

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

CHANNEL_ID_ENV = os.getenv('DISCORD_CHANNEL_ID')
NOTIFICATION_CHANNEL_ID = int(CHANNEL_ID_ENV) if CHANNEL_ID_ENV and CHANNEL_ID_ENV.isdigit() else None

API_URL = "https://api-community.plaync.com/aion2/board/cm_story_ko/article/search/moreArticle?isVote=true&moreSize=18&moreDirection=BEFORE&previousArticleId=0"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Referer": "https://aion2.plaync.com/",
    "Accept": "application/json"
}

last_seen_id = None

# --------------------------------------------------
# 3. API 직접 호출 함수
# --------------------------------------------------
async def fetch_latest_posts(limit=5):
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(API_URL, headers=HEADERS, timeout=10) as response:
                if response.status != 200:
                    print(f"[ERROR] API 응답 에러 (상태 코드: {response.status})", flush=True)
                    return []
                
                data = await response.json()
                
                articles = []
                if isinstance(data, list):
                    articles = data
                elif isinstance(data, dict):
                    articles = data.get("list") or data.get("articles") or data.get("contents") or data.get("documents") or []

                posts = []
                for item in articles[:limit]:
                    title = item.get("title") or item.get("subject") or "제목 없음"
                    article_id = item.get("articleId") or item.get("id")
                    
                    link = f"https://aion2.plaync.com/ko-kr/board/cmstory/view?articleId={article_id}" if article_id else "https://aion2.plaync.com/ko-kr/board/cmstory/list"
                    
                    posts.append({
                        "id": str(article_id),
                        "title": title.strip(),
                        "link": link
                    })
                    
                return posts

        except Exception as e:
            print(f"[ERROR] API 요청 오류: {e}", flush=True)
            return []

# --------------------------------------------------
# 4. 이벤트 및 자동 감지 로직
# --------------------------------------------------
@bot.event
async def on_ready():
    print(f"[INFO] 디스코드 봇 로그인 성공: {bot.user.name}", flush=True)
    if NOTIFICATION_CHANNEL_ID:
        print(f"[INFO] 알림 대상 채널 ID: {NOTIFICATION_CHANNEL_ID}", flush=True)
    else:
        print("[WARN] DISCORD_CHANNEL_ID 환경 변수가 비어있거나 올바르지 않습니다.", flush=True)
        
    if not auto_check_update.is_running():
        auto_check_update.start()

@tasks.loop(minutes=5)
async def auto_check_update():
    global last_seen_id
    
    if not NOTIFICATION_CHANNEL_ID:
        return

    channel = bot.get_channel(NOTIFICATION_CHANNEL_ID)
    if not channel:
        return

    posts = await fetch_latest_posts(limit=5)
    if not posts:
        return

    if last_seen_id is None:
        last_seen_id = posts[0]['id']
        print(f"[INFO] 최초 기준점 저장 (Article ID: {last_seen_id})", flush=True)
        return

    new_posts = []
    for post in posts:
        if post['id'] == last_seen_id:
            break
        new_posts.append(post)

    if new_posts:
        last_seen_id = new_posts[0]['link']
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
    if not TOKEN:
        print("[CRITICAL] DISCORD_TOKEN 환경 변수가 설정되지 않았습니다!", flush=True)
    else:
        bot.run(TOKEN)

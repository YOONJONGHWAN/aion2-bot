import os
import asyncio
import traceback
from threading import Thread
from flask import Flask
import aiohttp
import discord
from discord.ext import commands, tasks

# --------------------------------------------------
# 1. Render 웹 서버 유지용 Flask 설정
# --------------------------------------------------
app = Flask('')

@app.route('/')
def home():
    return "AION2 Bot is running!"

def run_flask():
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
    "Origin": "https://aion2.plaync.com",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7"
}

last_seen_id = None

# --------------------------------------------------
# 3. JSON 탐색 및 파싱 헬퍼 함수
# --------------------------------------------------
def find_articles_list(obj):
    """JSON 객체 구조 내부를 자동 탐색하여 게시글 배열(List)을 추출"""
    if isinstance(obj, list):
        if len(obj) > 0 and isinstance(obj[0], dict):
            return obj
        return []
    if isinstance(obj, dict):
        # 알려진 주요 키 우선 탐색
        for key in ["list", "articles", "contents", "documents", "data", "result"]:
            if key in obj:
                res = find_articles_list(obj[key])
                if res:
                    return res
        # 전체 딕셔너리 순회 탐색
        for k, v in obj.items():
            res = find_articles_list(v)
            if res:
                return res
    return []

def extract_post_info(item):
    """게시글 아이템에서 Title 및 ID 추출 (중첩 객체 지원)"""
    if not isinstance(item, dict):
        return None
    
    target = item
    if "article" in item and isinstance(item["article"], dict):
        target = item["article"]
    elif "data" in item and isinstance(item["data"], dict):
        target = item["data"]

    article_id = (
        target.get("articleId") or target.get("id") or target.get("article_id")
        or item.get("articleId") or item.get("id") or item.get("article_id")
    )
    
    title = (
        target.get("title") or target.get("subject") or target.get("name")
        or item.get("title") or item.get("subject") or item.get("name")
    )

    if article_id or title:
        return {
            "id": str(article_id) if article_id else "0",
            "title": str(title).strip() if title else "제목 없음"
        }
    return None

# --------------------------------------------------
# 4. API 직접 호출 함수
# --------------------------------------------------
async def fetch_latest_posts(limit=5):
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(API_URL, headers=HEADERS, timeout=10) as response:
                print(f"[DEBUG] API 응답 상태 코드: {response.status}", flush=True)
                
                if response.status != 200:
                    print(f"[ERROR] API 요청 실패 (상태 코드: {response.status})", flush=True)
                    return []
                
                data = await response.json()
                
                articles_raw = find_articles_list(data)
                
                if not articles_raw:
                    # 데이터 구조 파악용 원본 출력
                    print(f"[DEBUG] JSON 구조 분석용 데이터 샘플: {str(data)[:300]}", flush=True)

                posts = []
                for item in articles_raw[:limit]:
                    info = extract_post_info(item)
                    if info:
                        article_id = info["id"]
                        link = f"https://aion2.plaync.com/ko-kr/board/cmstory/view?articleId={article_id}" if article_id != "0" else "https://aion2.plaync.com/ko-kr/board/cmstory/list"
                        posts.append({
                            "id": article_id,
                            "title": info["title"],
                            "link": link
                        })
                    
                print(f"[DEBUG] 최종 추출된 게시글 수: {len(posts)}개", flush=True)
                return posts

        except Exception as e:
            print(f"[ERROR] API 데이터 수신/파싱 오류 발생:", flush=True)
            traceback.print_exc()
            return []

# --------------------------------------------------
# 5. 이벤트 및 자동 감지 로직
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
        last_seen_id = new_posts[0]['id']
        for post in reversed(new_posts):
            await channel.send(f"📢 **[새 게시글] {post['title']}**\n🔗 {post['link']}")

# --------------------------------------------------
# 6. 수동 명령어 (!확인)
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
# 7. 실행
# --------------------------------------------------
if __name__ == "__main__":
    keep_alive()
    if not TOKEN:
        print("[CRITICAL] DISCORD_TOKEN 환경 변수가 설정되지 않았습니다!", flush=True)
    else:
        bot.run(TOKEN)

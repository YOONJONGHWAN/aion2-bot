import os
import re
import time
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
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

# Gemini 모델 지정 (404 지속 시 'gemini-2.0-flash' 또는 'gemini-1.5-flash'로 변경 가능)
GEMINI_MODEL = "gemini-2.0-flash"

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

def clean_html(text):
    if not text:
        return ""
    clean = re.sub(r'<[^>]+>', '', str(text))
    return clean.strip()

# --------------------------------------------------
# 3. Gemini AI 3줄 요약 함수
# --------------------------------------------------
async def summarize_with_gemini(title, content):
    if not GEMINI_API_KEY:
        print("[WARN] GEMINI_API_KEY 환경변수가 설정되어 있지 않습니다.", flush=True)
        return None

    start_time = time.time()
    text_to_summarize = clean_html(content)
    if len(text_to_summarize) < 30:
        text_to_summarize = f"제목: {title}"

    gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    
    prompt = (
        "너는 아이온2 디스코드 알림 봇이야. 아래 게임 공지사항/게시글을 읽고 유저들이 읽기 쉽게 핵심만 3줄로 요약해줘.\n"
        "조건:\n"
        "1. 각 줄은 '- '로 시작할 것\n"
        "2. 군더더기 없이 핵심 변경사항/이벤트 내용만 명확히 요약할 것\n\n"
        f"[제목]: {title}\n"
        f"[내용]: {text_to_summarize[:1500]}"
    )

    payload = {
        "contents": [{"parts": [{"text": prompt}]}]
    }

    req_headers = {
        "Content-Type": "application/json"
    }

    async with aiohttp.ClientSession() as session:
        try:
            timeout = aiohttp.ClientTimeout(total=5)
            async with session.post(gemini_url, headers=req_headers, json=payload, timeout=timeout) as response:
                elapsed = time.time() - start_time
                if response.status == 200:
                    res_json = await response.json()
                    summary_text = res_json['candidates'][0]['content']['parts'][0]['text']
                    print(f"[INFO] AI 요약 완료 (소요시간: {elapsed:.2f}초)", flush=True)
                    return summary_text.strip()
                else:
                    err_text = await response.text()
                    print(f"[WARN] Gemini API 실패 (코드: {response.status}, 소요시간: {elapsed:.2f}초) - 상세내용: {err_text}", flush=True)
                    return None
        except asyncio.TimeoutError:
            print(f"[WARN] Gemini API 5초 타임아웃 발생 (요약 생략)", flush=True)
            return None
        except Exception as e:
            print(f"[WARN] AI 요약 예외 발생: {e}", flush=True)
            return None

# --------------------------------------------------
# 4. JSON 파싱 헬퍼 함수
# --------------------------------------------------
def find_articles_list(obj):
    if isinstance(obj, list):
        if len(obj) > 0 and isinstance(obj[0], dict):
            return obj
        return []
    if isinstance(obj, dict):
        for key in ["list", "articles", "contents", "documents", "data", "result"]:
            if key in obj:
                res = find_articles_list(obj[key])
                if res:
                    return res
        for k, v in obj.items():
            res = find_articles_list(v)
            if res:
                return res
    return []

def extract_post_info(item):
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

    raw_content = (
        target.get("contents") or target.get("content") or target.get("body") or
        target.get("summary") or item.get("contents") or item.get("content") or ""
    )

    image_url = (
        target.get("thumbnailUrl") or target.get("thumbnail") or 
        target.get("imageUrl") or target.get("image") or 
        target.get("coverImageUrl") or target.get("posterUrl") or
        item.get("thumbnailUrl") or item.get("thumbnail") or
        item.get("imageUrl") or item.get("image")
    )

    if not image_url and isinstance(target.get("images"), list) and len(target["images"]) > 0:
        first_img = target["images"][0]
        if isinstance(first_img, str):
            image_url = first_img
        elif isinstance(first_img, dict):
            image_url = first_img.get("url") or first_img.get("src")

    if image_url and isinstance(image_url, str) and image_url.startswith("/"):
        image_url = f"https://api-community.plaync.com{image_url}"

    if article_id or title:
        return {
            "id": str(article_id) if article_id else "0",
            "title": str(title).strip() if title else "제목 없음",
            "content": raw_content,
            "image": image_url
        }
    return None

# --------------------------------------------------
# 5. API 호출 함수
# --------------------------------------------------
async def fetch_latest_posts(limit=5):
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(API_URL, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=5)) as response:
                if response.status != 200:
                    return []
                
                data = await response.json()
                articles_raw = find_articles_list(data)

                posts = []
                for item in articles_raw[:limit]:
                    info = extract_post_info(item)
                    if info:
                        article_id = info["id"]
                        link = f"https://aion2.plaync.com/ko-kr/board/cmstory/view?articleId={article_id}" if article_id != "0" else "https://aion2.plaync.com/ko-kr/board/cmstory/list"
                        posts.append({
                            "id": article_id,
                            "title": info["title"],
                            "content": info["content"],
                            "link": link,
                            "image": info["image"]
                        })
                return posts

        except Exception as e:
            print(f"[ERROR] API 데이터 수신 오류: {e}", flush=True)
            return []

# --------------------------------------------------
# 6. 자동 알림 감지
# --------------------------------------------------
@bot.event
async def on_ready():
    print(f"[INFO] 디스코드 봇 로그인 성공: {bot.user.name}", flush=True)
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
        
        # 신규 글들 요약 병렬 처리
        tasks_list = [summarize_with_gemini(p['title'], p['content']) for p in reversed(new_posts)]
        summaries = await asyncio.gather(*tasks_list)

        for post, summary in zip(reversed(new_posts), summaries):
            embed = discord.Embed(
                title=f"📢 {post['title']}",
                url=post['link'],
                color=discord.Color.blue()
            )
            if summary:
                embed.add_field(name="📝 **AI 3줄 요약**", value=summary, inline=False)
            embed.add_field(name="🔗 바로가기", value=f"[게시글 읽기]({post['link']})", inline=False)
            if post['image']:
                embed.set_image(url=post['image'])
                
            await channel.send(embed=embed)

# --------------------------------------------------
# 7. 수동 명령어 (!확인)
# --------------------------------------------------
@bot.command(name='확인')
async def check_update(ctx):
    await ctx.send("CM 스토리 최신 게시글과 AI 요약을 가져오는 중입니다...")
    posts = await fetch_latest_posts(limit=2)
    
    if posts:
        summary_tasks = [summarize_with_gemini(post['title'], post['content']) for post in posts]
        summaries = await asyncio.gather(*summary_tasks)

        for post, summary in zip(posts, summaries):
            embed = discord.Embed(
                title=post['title'],
                url=post['link'],
                color=discord.Color.gold()
            )
            if summary:
                embed.add_field(name="📝 **AI 3줄 요약**", value=summary, inline=False)
            embed.add_field(name="🔗 바로가기", value=f"[게시글 읽기]({post['link']})", inline=False)
            if post['image']:
                embed.set_image(url=post['image'])
                
            await ctx.send(embed=embed)
    else:
        await ctx.send("게시글을 불러오지 못했습니다.")

# --------------------------------------------------
# 8. 실행
# --------------------------------------------------
if __name__ == "__main__":
    keep_alive()
    if TOKEN:
        bot.run(TOKEN)

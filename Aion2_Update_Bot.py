import os
import re
import sys
import time
import asyncio
import threading
import discord
from discord.ext import commands, tasks
from flask import Flask
from google import genai
from playwright.async_api import async_playwright

# --------------------------------------------------
# 1. 환경 변수 및 구글 AI SDK 설정
# --------------------------------------------------
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip().strip('"').strip("'")
# 자동 알림을 전송할 디스코드 채널 ID (Render Environment에 DISCORD_CHANNEL_ID 추가 필요)
CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))

client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

# --------------------------------------------------
# 2. Render 24시간 작동용 Flask 웹 서버 설정
# --------------------------------------------------
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# --------------------------------------------------
# 3. 유틸리티 함수 (HTML 태그 제거 및 상세 AI 요약)
# --------------------------------------------------
def clean_html(raw_html):
    if not raw_html:
        return ""
    cleanr = re.compile('<.*?>')
    cleantext = re.sub(cleanr, '', raw_html)
    return cleantext.strip()

async def summarize_with_gemini(title, content):
    if not GEMINI_API_KEY or not client:
        print("[WARN] GEMINI_API_KEY 환경변수가 설정되어 있지 않습니다.", flush=True)
        return None

    start_time = time.time()
    text_to_summarize = clean_html(content)
    if len(text_to_summarize) < 30:
        text_to_summarize = f"제목: {title}"

    prompt = (
        "너는 아이온2 디스코드 알림 봇이야. 아래 게임 공지사항/게시글을 읽고 유저들이 꼭 알아야 할 중요한 내용을 알차게 정리해줘.\n"
        "조건:\n"
        "1. 줄 수에 구애받지 말고, 주요 점검 시간, 핵심 변경사항, 신규 이벤트, 보상, 주의사항 등 중요한 정보가 빠짐없이 포함되도록 작성할 것\n"
        "2. 유저들이 읽기 쉽게 각 항목은 '- '로 시작하여 가독성 높게 정리할 것\n"
        "3. 군더더기 서론이나 인사말 없이 핵심 요약 내용만 출력할 것\n\n"
        f"[제목]: {title}\n"
        f"[내용]: {text_to_summarize[:2500]}"
    )

    candidate_models = ['gemini-3.5-flash', 'gemini-3.1-flash-lite', 'gemini-3.5-flash-lite']
    loop = asyncio.get_running_loop()

    for model_name in candidate_models:
        try:
            def call_api(m=model_name):
                return client.models.generate_content(model=m, contents=prompt)

            response = await asyncio.wait_for(
                loop.run_in_executor(None, call_api),
                timeout=12.0
            )

            if response and response.text:
                elapsed = time.time() - start_time
                print(f"[INFO] AI 요약 성공 (사용 모델: {model_name}, 소요시간: {elapsed:.2f}초)", flush=True)
                return response.text.strip()

        except asyncio.TimeoutError:
            print(f"[WARN] {model_name} 모델 타임아웃 초과, 다음 모델 시도 중...", flush=True)
        except Exception as e:
            print(f"[WARN] {model_name} 모델 호출 실패, 다음 모델 시도 중... (사유: {e})", flush=True)

    return None

# --------------------------------------------------
# 4. 디스코드 봇 설정 및 이벤트/명령어
# --------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

last_article_id = None  # 감지된 최신 게시글 ID 저장 변수

@bot.event
async def on_ready():
    print(f"[INFO] 디스코드 봇 로그인 성공: {bot.user.name}", flush=True)
    if not check_updates.is_running():
        check_updates.start()

@bot.command(name="확인")
async def check_command(ctx):
    await ctx.send("공지사항 수동 확인을 진행합니다...")
    await fetch_and_notify(target_channel=ctx.channel, force_send=True)

# --------------------------------------------------
# 5. Playwright 웹 크롤링 및 공지 감지 로직
# --------------------------------------------------
async def fetch_and_notify(target_channel=None, force_send=False):
    global last_article_id

    url = "https://aion2.plaync.com/ko-kr/board/cm_story/list"

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(url, timeout=60000, wait_until="domcontentloaded")
            await asyncio.sleep(3)  # 동적 콘텐츠 로딩 대기

            # 목록에서 첫 번째 게시글 요소 탐색
            articles = await page.query_selector_all("a[href*='articleId=']")
            if not articles:
                print("[WARN] 게시글 목록을 찾을 수 없습니다.", flush=True)
                await browser.close()
                return

            latest_article = articles[0]
            href = await latest_article.get_attribute("href")
            
            # Article ID 추출
            article_id_match = re.search(r'articleId=([a-zA-Z0-9]+)', href or "")
            current_id = article_id_match.group(1) if article_id_match else href

            # 최신 글 여부 확인 (최초 실행 시에는 기준점만 저장)
            if last_article_id is None and not force_send:
                last_article_id = current_id
                print(f"[INFO] 최초 기준 게시글 ID 저장: {last_article_id}", flush=True)
                await browser.close()
                return

            if current_id == last_article_id and not force_send:
                print("[INFO] 새로운 공지사항이 없습니다.", flush=True)
                await browser.close()
                return

            # 새 글 발견 시 상세 페이지 클릭하여 본문 수집
            detail_url = f"https://aion2.plaync.com{href}" if href.startswith('/') else href
            await page.goto(detail_url, timeout=60000, wait_until="domcontentloaded")
            await asyncio.sleep(2)

            # 제목, 본문, 이미지 추출
            title_el = await page.query_selector("h1, .title, .board-view__title")
            title = await title_el.inner_text() if title_el else "아이온2 신규 공지사항"

            content_el = await page.query_selector(".board-view__content, .contents, .article-body")
            content = await content_el.inner_text() if content_el else ""

            img_el = await page.query_selector(".board-view__content img, .article-body img")
            img_url = await img_el.get_attribute("src") if img_el else None

            await browser.close()

            # AI 요약 생성
            summary = await summarize_with_gemini(title, content)

            # 디스코드 임베드 생성
            embed = discord.Embed(
                title=f"📢 {title}",
                url=detail_url,
                color=0x00ff00
            )

            if summary:
                embed.add_field(name="🤖 AI 주요 내용 요약", value=summary, inline=False)
            else:
                embed.add_field(name="📝 공지 내용", value=clean_html(content)[:500] + "...", inline=False)

            embed.add_field(name="🔗 공지 바로가기", value=f"[공지사항 전체보기]({detail_url})", inline=False)

            if img_url:
                embed.set_image(url=img_url)

            # 메세지 전송
            if target_channel:
                await target_channel.send(embed=embed)
            elif CHANNEL_ID:
                channel = bot.get_channel(CHANNEL_ID)
                if channel:
                    await channel.send(embed=embed)

            if not force_send:
                last_article_id = current_id
                print(f"[INFO] 새 공지사항 알림 전송 완료! (ID: {last_article_id})", flush=True)

    except Exception as e:
        print(f"[ERROR] 크롤링 중 오류 발생: {e}", flush=True)

@tasks.loop(minutes=5)
async def check_updates():
    await fetch_and_notify()

@check_updates.before_loop
async def before_check_updates():
    await bot.wait_until_ready()

# --------------------------------------------------
# 6. 메인 실행부
# --------------------------------------------------
if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    if DISCORD_TOKEN:
        bot.run(DISCORD_TOKEN)
    else:
        print("[ERROR] DISCORD_TOKEN 환경변수가 설정되지 않았습니다.", flush=True)

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
CHANNEL_ID = os.getenv("DISCORD_CHANNEL_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip().strip('"').strip("'")

client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
BOARD_URL = "https://aion2.plaync.com/ko-kr/board/cm_story/list"

# --------------------------------------------------
# 2. Render 24시간 작동용 Flask 웹 서버
# --------------------------------------------------
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# --------------------------------------------------
# 3. 크롤링 및 세분화 AI 요약 함수
# --------------------------------------------------
def clean_html(raw_html):
    if not raw_html:
        return ""
    cleanr = re.compile('<.*?>')
    cleantext = re.sub(cleanr, '', raw_html)
    return cleantext.strip()

async def fetch_latest_article():
    """실제 아이온2 홈페이지에서 최신 공지사항 1건을 크롤링해 오는 함수"""
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            
            await page.goto(BOARD_URL, timeout=30000)
            await page.wait_for_selector("a[href*='articleId=']", timeout=15000)

            articles = await page.query_selector_all("a[href*='articleId=']")
            if not articles:
                await browser.close()
                return None

            first_article = articles[0]
            href = await first_article.get_attribute("href")
            
            match = re.search(r'articleId=([a-zA-Z0-9]+)', href)
            if not match:
                await browser.close()
                return None

            article_id = match.group(1)
            full_url = f"https://aion2.plaync.com{href}" if href.startswith('/') else href

            # 상세 페이지 접속
            await page.goto(full_url, timeout=30000)
            await asyncio.sleep(2)

            title_el = await page.query_selector("h1, .title, .board-title")
            title = await title_el.inner_text() if title_el else "아이온2 신규 공지사항"

            content_el = await page.query_selector(".contents, .board-contents, .article-body")
            content = await content_el.inner_text() if content_el else ""

            img_el = await page.query_selector(".contents img, .board-contents img, .article-body img")
            image_url = await img_el.get_attribute("src") if img_el else None

            await browser.close()
            
            return {
                "id": article_id,
                "url": full_url,
                "title": title.strip(),
                "content": content.strip(),
                "image_url": image_url
            }
    except Exception as e:
        print(f"[ERROR] 크롤링 실패: {e}", flush=True)
        return None

async def summarize_with_gemini(title, content):
    if not GEMINI_API_KEY or not client:
        print("[WARN] GEMINI_API_KEY 환경변수가 설정되어 있지 않습니다.", flush=True)
        return None

    start_time = time.time()
    text_to_summarize = clean_html(content)
    if len(text_to_summarize) < 30:
        text_to_summarize = f"제목: {title}"

    # 📌 세분화 및 상세 요약을 위한 개선된 프롬프트
    prompt = (
        "너는 아이온2 디스코드 알림 봇이야. 아래 게임 공지사항을 읽고 유저들이 한눈에 파악할 수 있도록 구체적이고 상세하게 정리해줘.\n\n"
        "아래 [출력 양식]을 바탕으로 작성하되, 해당 내용이 공지에 없는 항목은 생략해줘.\n\n"
        "[출력 양식]\n"
        "📌 **주요 업데이트 및 점검 내용**\n"
        "- (점검 시간, 신규 콘텐츠, 핵심 변경사항 등)\n\n"
        "🛠 **추가 및 개선 사항**\n"
        "- (시스템 변경, 밸런스 패치, 오류 수정 등)\n\n"
        "🎁 **보상 및 이벤트 정보**\n"
        "- (점검 보상, 진행되는 이벤트, 수령 방법 등)\n\n"
        "⚠️ **주의 및 안내 사항**\n"
        "- (유저 주의사항, 사전 데이터 보호 조치 등)\n\n"
        "작성 조건:\n"
        "1. 단어만 늘어놓지 말고 구체적인 시간, 아이템 이름, 수량, 핵심 수치 등을 명확히 기재할 것.\n"
        "2. 군더더기 서론/결론, 인사말 없이 지정된 양식 항목만 간결하고 명확하게 출력할 것.\n\n"
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
            print(f"[WARN] {model_name} 모델 12초 타임아웃 초과, 다음 모델 시도 중...", flush=True)
        except Exception as e:
            print(f"[WARN] {model_name} 모델 호출 실패, 다음 모델 시도 중... (사유: {e})", flush=True)

    return None

# --------------------------------------------------
# 4. 디스코드 봇 설정 및 이벤트/명령어
# --------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

last_article_id = None

@bot.event
async def on_ready():
    print(f"[INFO] 디스코드 봇 로그인 성공: {bot.user.name}", flush=True)
    if not check_updates.is_running():
        check_updates.start()

@bot.command(name="확인")
async def check_command(ctx):
    await ctx.send("🔍 홈페이지에서 최신 공지사항을 직접 확인하는 중입니다...")
    
    article = await fetch_latest_article()
    if not article:
        await ctx.send("❌ 공지사항을 불러오는 데 실패했습니다. 잠시 후 다시 시도해 주세요.")
        return

    summary = await summarize_with_gemini(article['title'], article['content'])
    
    embed = discord.Embed(title=f"📢 {article['title']}", url=article['url'], color=0x00ff00)
    
    if summary:
        embed.add_field(name="🤖 AI 주요 내용 요약", value=summary, inline=False)
    else:
        embed.add_field(name="📝 공지 내용", value=clean_html(article['content'])[:500], inline=False)
    
    embed.add_field(name="🔗 공지 바로가기", value=f"[공지사항 전체보기]({article['url']})", inline=False)
    if article['image_url']:
        embed.set_image(url=article['image_url'])
        
    await ctx.send(embed=embed)

@bot.command(name="테스트알림")
async def test_notification_command(ctx):
    """새 공지가 등록된 상황을 강제로 연출하여 자동 알림 시스템 전체를 검증합니다."""
    global last_article_id
    await ctx.send("🧪 **[자동 알림 시스템 검증]** 최신 공지를 새 글인 것처럼 감지하여 자동 알림을 테스트합니다...")
    
    # 강제로 이전 ID 기준을 가짜 값으로 변경
    last_article_id = "test_dummy_id"
    
    # 5분 주기 크롤링 실행 로직 호출
    await do_check_updates()

# --------------------------------------------------
# 5. 5분 주기 크롤링 및 새 글 자동 감지 로직
# --------------------------------------------------
async def do_check_updates():
    global last_article_id
    
    if not CHANNEL_ID:
        print("[WARN] DISCORD_CHANNEL_ID 환경변수가 설정되지 않았습니다.", flush=True)
        return

    channel = bot.get_channel(int(CHANNEL_ID))
    if not channel:
        print(f"[WARN] 채널을 찾을 수 없습니다. (Channel ID: {CHANNEL_ID})", flush=True)
        return

    try:
        article = await fetch_latest_article()
        if not article:
            return

        # 최초 실행 시 현재 최신 공지를 기준점으로 기록
        if last_article_id is None:
            last_article_id = article['id']
            print(f"[INFO] 최초 기준 공지 ID 저장 완료: {last_article_id}", flush=True)
            return

        # 새로운 공지가 추가되었을 때 디스코드 채널로 자동 전송
        if article['id'] != last_article_id:
            print(f"[NEW] 새 공지사항 감지! (ID: {article['id']})", flush=True)
            
            summary = await summarize_with_gemini(article['title'], article['content'])

            embed = discord.Embed(title=f"📢 {article['title']}", url=article['url'], color=0x00ff00)
            
            if summary:
                embed.add_field(name="🤖 AI 주요 내용 요약", value=summary, inline=False)
            else:
                embed.add_field(name="📝 공지 내용", value=clean_html(article['content'])[:500], inline=False)

            embed.add_field(name="🔗 공지 바로가기", value=f"[공지사항 전체보기]({article['url']})", inline=False)
            if article['image_url']:
                embed.set_image(url=article['image_url'])

            await channel.send(embed=embed)
            last_article_id = article['id']

    except Exception as e:
        print(f"[ERROR] 자동 감지 크롤링 중 오류 발생: {e}", flush=True)

@tasks.loop(minutes=5)
async def check_updates():
    await do_check_updates()

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

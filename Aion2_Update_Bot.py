import os
import json
import re
import asyncio
import time
import logging
import threading
import discord
from discord.ext import commands, tasks
from playwright.async_api import async_playwright
from google import genai
from flask import Flask, jsonify

# ==========================================
# 1. 로깅 및 환경변수 설정
# ==========================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)-8s] %(message)s")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
DISCORD_CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

BOARD_URL = "https://aion2.plaync.com/ko-kr/board/cm_story/list"
BASE_URL = "https://aion2.plaync.com"

STATE_FILE = "bot_state.json"
PAGE_TIMEOUT = 15000  # 목록 수집 타임아웃 (15초)
DETAIL_TIMEOUT = 8000 # 상세 페이지 수집 타임아웃 (8초)
MAX_ARTICLES_TO_SCAN = 20

# ==========================================
# 2. Flask 웹서버 (Render Health Check)
# ==========================================
app = Flask("Aion2_Update_Bot")

@app.route("/")
def health_check():
    return jsonify(status="ok", service="Aion2 Update Bot", timestamp=time.time()), 200

def run_flask():
    app.run(host="0.0.0.0", port=10000)

threading.Thread(target=run_flask, daemon=True).start()

# ==========================================
# 3. 디스코드 봇 설정
# ==========================================
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ==========================================
# 4. 상태 저장/로드 (Atomic Write 적용)
# ==========================================
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"상태 파일 로드 실패: {e}")
    return {"seen_ids": [], "auto_enabled": True}

def save_state(state):
    tmp_file = f"{STATE_FILE}.tmp"
    try:
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp_file, STATE_FILE)
    except Exception as e:
        logging.error(f"상태 파일 저장 실패: {e}")

# ==========================================
# 5. Gemini AI 요약 (3단계 폴백 및 타임아웃)
# ==========================================
async def generate_ai_summary(title, content):
    if not GEMINI_API_KEY or not content.strip():
        return None

    client = genai.Client(api_key=GEMINI_API_KEY)
    prompt = f"다음은 게임 '아이온2'의 공지사항입니다. 핵심 내용을 디스코드 알림용으로 3줄 이내로 간결하게 요약해 주세요.\n\n제목: {title}\n본문:\n{content[:2000]}"
    models = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]

    for model in models:
        try:
            def _call():
                return client.models.generate_content(model=model, contents=prompt)
            
            response = await asyncio.to_thread(_call)
            if response and response.text:
                return response.text.strip()
        except Exception as e:
            logging.warning(f"Gemini 모델 ({model}) 호출 실패: {e}")
            
    return None

# ==========================================
# 6. Playwright 수집 로직 (타임아웃 보강)
# ==========================================
async def fetch_article_list(page):
    logging.info("게시글 목록 수집 시작...")
    try:
        await page.goto(BOARD_URL, timeout=PAGE_TIMEOUT, wait_until="domcontentloaded")
        
        try:
            await page.wait_for_selector("a[href*='articleId='], a[href*='/board/']", timeout=8000)
        except Exception:
            pass

        await page.wait_for_timeout(1000)

        links = await page.query_selector_all("a[href*='articleId=']")
        if not links:
            links = await page.query_selector_all("a[href*='/board/']")

        articles = []
        seen_ids_in_page = set()

        for link in links:
            href = await link.get_attribute("href")
            if not href:
                continue

            full_url = href if href.startswith("http") else f"{BASE_URL}{href}"
            match = re.search(r"(?:articleId=|/view/|/board/)([^&?/]+)", href)
            
            if match:
                art_id = match.group(1)
                if art_id in seen_ids_in_page:
                    continue
                seen_ids_in_page.add(art_id)

                try:
                    title_text = (await link.inner_text()).strip()
                    title = title_text.split("\n")[0] if title_text else "제목 없음"
                except Exception:
                    title = "제목 없음"

                articles.append({"id": art_id, "title": title, "url": full_url})
                if len(articles) >= MAX_ARTICLES_TO_SCAN:
                    break

        logging.info(f"게시글 {len(articles)}개 발견")
        return articles
    except Exception as e:
        logging.error(f"게시글 목록 수집 실패: {e}")
        return []

# ==========================================
# 본문 수집 로직 (엔씨소프트 SPA 렌더링 대응)
# ==========================================
async def fetch_article_detail(page, url):
    logging.info(f"상세 페이지 접속 시도: {url}")
    try:
        # DOM 로드 완료까지 접속
        await page.goto(url, timeout=DETAIL_TIMEOUT, wait_until="domcontentloaded")

        # 엔씨소프트 게시판에서 사용되는 본문 영역 선택자 후보군
        selectors = [
            "div[class*='content']",
            "div[class*='detail']",
            "div[class*='view']",
            ".board_view",
            ".article_view",
            ".board-detail",
            "article"
        ]

        # 본문 요소가 자바스크립트로 화면에 그려질 때까지 대기 후 추출
        for sel in selectors:
            try:
                elem = await page.wait_for_selector(sel, timeout=3000)
                if elem:
                    text = (await elem.inner_text()).strip()
                    # 메뉴나 불필요한 텍스트 제외, 유효 본문(30자 이상) 확보 시 즉시 반환
                    if len(text) >= 30:
                        logging.info(f"[SUCCESS] 본문 추출 성공 ({len(text)}자 추출됨 / 선택자: {sel})")
                        return text
            except Exception:
                continue

        # 선택자 탐색 실패 시 body 전체에서 텍스트 수집 (최후의 보루)
        body = await page.query_selector("body")
        if body:
            text = (await body.inner_text()).strip()
            if len(text) > 50:
                logging.info(f"[FALLBACK] body 태그 전체 텍스트 추출 ({len(text)}자)")
                return text[:3000]

    except Exception as e:
        logging.warning(f"[WARN] 상세 페이지 수집 타임아웃 또는 실패: {e}")

    logging.error("[FAIL] 본문 텍스트를 추출하지 못했습니다.")
    return ""


# ==========================================
# AI 요약 생성 로직 (제목+본문 조합)
# ==========================================
async def generate_ai_summary(title, content):
    if not GEMINI_API_KEY:
        logging.warning("GEMINI_API_KEY가 설정되지 않아 요약을 건너뜁니다.")
        return None

    # 본문이 비어있으면 제목이라도 넘겨서 요약/성격 파악 시도
    target_text = content[:2000] if content.strip() else "본문 내용 없음 (제목 기반 파악 필요)"

    prompt = (
        f"당신은 게임 '아이온2' 공지사항 요약 도우미입니다.\n"
        f"아래 공지사항을 바탕으로 디스코드 유저들이 빠르게 읽을 수 있도록 핵심 내용 3줄 요약(문장 형태)을 작성해 주세요.\n\n"
        f"📌 공지 제목: {title}\n"
        f"📄 공지 본문:\n{target_text}\n\n"
        f"규칙: 인사말이나 서론 없이, 디스코드 알림에 들어갈 핵심 3줄 요약 내용만 출력하세요."
    )

    client = genai.Client(api_key=GEMINI_API_KEY)
    models = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]

    for model in models:
        try:
            def _call():
                return client.models.generate_content(model=model, contents=prompt)

            response = await asyncio.to_thread(_call)
            if response and response.text:
                summary_result = response.text.strip()
                logging.info(f"[SUCCESS] Gemini ({model}) 요약 생성 완료")
                return summary_result
        except Exception as e:
            logging.warning(f"Gemini 모델 ({model}) 호출 실패: {e}")

    return None

# ==========================================
# 7. 디스코드 전송 및 2중 중복 검사
# ==========================================
async def is_already_posted(channel, article_id):
    state = load_state()
    if article_id in state.get("seen_ids", []):
        return True

    try:
        async for msg in channel.history(limit=100):
            if msg.embeds:
                for embed in msg.embeds:
                    if embed.url and article_id in embed.url:
                        return True
    except Exception as e:
        logging.warning(f"디스코드 내역 조회 실패: {e}")

    return False

async def build_and_send_embed(target_channel, article, content_text, is_test=False):
    title = article["title"]
    url = article["url"]

    summary = None
    if content_text:
        try:
            # AI 요약 10초 타임아웃 적용
            summary = await asyncio.wait_for(generate_ai_summary(title, content_text), timeout=10.0)
        except Exception as e:
            logging.warning(f"AI 요약 타임아웃/실패: {e}")

    embed = discord.Embed(
        title=f"{'[테스트] ' if is_test else '📢 '} {title}",
        url=url,
        color=0x00ff00 if not is_test else 0xffa500,
        timestamp=discord.utils.utcnow()
    )
    if summary:
        embed.description = summary
        embed.set_footer(text="AI 요약 제공 | 아이온2 공식 공지")
    else:
        embed.description = "상세 내용을 불러오지 못해 원본 링크를 안내합니다. 클릭하여 확인하세요."
        embed.set_footer(text="아이온2 공식 공지")

    return await target_channel.send(embed=embed)

# ==========================================
# 8. 백그라운드 자동 감지 루프 (5분 주기)
# ==========================================
@tasks.loop(minutes=5)
async def auto_check_loop():
    state = load_state()
    if not state.get("auto_enabled", True):
        return

    channel = bot.get_channel(DISCORD_CHANNEL_ID)
    if not channel:
        return

    logging.info("[CHECK] 5분 주기 공지 확인 시작")
    browser = None
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"]
            )
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800},
                locale="ko-KR"
            )
            page = await context.new_page()

            articles = await fetch_article_list(page)
            if not articles:
                return

            # 최초 실행 시 기준점 저장만 수행
            if not state.get("seen_ids"):
                state["seen_ids"] = [a["id"] for a in articles]
                save_state(state)
                logging.info(f"최초 실행 기준점 저장 완료 ({len(articles)}개)")
                return

            new_targets = []
            for art in articles:
                if await is_already_posted(channel, art["id"]):
                    break  # Early Exit
                new_targets.append(art)

            # 신규 글 최대 3개 역순 처리 (오래된 글부터)
            for art in reversed(new_targets[:3]):
                content = await fetch_article_detail(page, art["url"])
                await build_and_send_embed(channel, art, content, is_test=False)

                state = load_state()
                if art["id"] not in state["seen_ids"]:
                    state["seen_ids"].append(art["id"])
                    save_state(state)

    except Exception as e:
        logging.error(f"자동 감지 루프 중 에러: {e}")
    finally:
        if browser:
            await browser.close()

# ==========================================
# 9. 명령어 구현 (!확인, !테스트알림 등)
# ==========================================
@bot.command(name="확인")
async def cmd_check(ctx):
    status_msg = await ctx.reply("🔍 실제 홈페이지에서 최신 공지사항을 확인하는 중입니다...")
    browser = None
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"])
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800},
                locale="ko-KR"
            )
            page = await context.new_page()
            articles = await fetch_article_list(page)

            if not articles:
                await status_msg.edit(content="❌ 공지사항 목록을 가져오지 못했습니다.")
                return

            latest = articles[0]
            await status_msg.edit(content=f"✅ 최신 공지 확인 완료!\n📌 **제목:** {latest['title']}\n🔗 **링크:** {latest['url']}")
    except Exception as e:
        logging.error(f"!확인 실행 실패: {e}")
        await status_msg.edit(content=f"❌ 확인 중 오류가 발생했습니다: `{e}`")
    finally:
        if browser:
            await browser.close()

@bot.command(name="테스트알림")
async def cmd_test(ctx):
    status_msg = await ctx.reply("🧪 실제 홈페이지의 최신 공지를 이용해 테스트를 진행합니다...")
    browser = None
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"])
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800},
                locale="ko-KR"
            )
            page = await context.new_page()
            articles = await fetch_article_list(page)

            if not articles:
                await status_msg.edit(content="❌ 테스트할 공지사항을 가져오지 못했습니다.")
                return

            target = articles[0]
            content = await fetch_article_detail(page, target["url"])
            await build_and_send_embed(ctx.channel, target, content, is_test=True)
            await status_msg.edit(content="✅ 테스트 알림 발송이 완료되었습니다!")
    except Exception as e:
        logging.error(f"!테스트알림 실행 실패: {e}")
        await status_msg.edit(content=f"❌ 테스트 중 오류가 발생했습니다: `{e}`")
    finally:
        if browser:
            await browser.close()

@bot.command(name="스케줄러시작")
async def cmd_start(ctx):
    state = load_state()
    state["auto_enabled"] = True
    save_state(state)
    await ctx.reply("▶️ 5분 주기 자동 공지 감지가 시작되었습니다.")

@bot.command(name="스케줄러중지")
async def cmd_stop(ctx):
    state = load_state()
    state["auto_enabled"] = False
    save_state(state)
    await ctx.reply("⏸️ 5분 주기 자동 공지 감지가 중지되었습니다.")

@bot.command(name="상태")
async def cmd_status(ctx):
    state = load_state()
    status_str = "동작 중 ▶️" if state.get("auto_enabled", True) else "중지됨 ⏸️"
    await ctx.reply(f"📊 **현재 봇 상태**\n- 자동 감지: {status_str}\n- 수집된 공지 수: {len(state.get('seen_ids', []))}개")

# ==========================================
# 10. 봇 구동 준비
# ==========================================
@bot.event
async def on_ready():
    logging.info(f"디스코드 봇 로그인 성공: {bot.user}")
    if not auto_check_loop.is_running():
        auto_check_loop.start()
        logging.info("5분 주기 자동 공지 감지 시작")

if __name__ == "__main__":
    if DISCORD_TOKEN:
        bot.run(DISCORD_TOKEN)
    else:
        logging.error("DISCORD_TOKEN 환경변수가 설정되지 않았습니다.")

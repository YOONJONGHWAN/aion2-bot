import os
import re
import json
import time
import asyncio
import threading
import subprocess
import sys
import traceback
from urllib.parse import urljoin, urlparse, parse_qs

import discord
from discord.ext import commands, tasks
from flask import Flask
from google import genai
from playwright.async_api import async_playwright

subprocess.run(
    [
        sys.executable,
        "-m",
        "playwright",
        "install",
        "chromium"
    ],
    check=True
)

print("[INFO] Playwright Chromium 설치/확인 완료", flush=True)


# ============================================================
# 1. 기본 설정
# ============================================================

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = os.getenv("DISCORD_CHANNEL_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip().strip('"').strip("'")

BOARD_URL = "https://aion2.plaync.com/ko-kr/board/cm_story/list"
BASE_URL = "https://aion2.plaync.com"

STATE_FILE = "aion2_state.json"

# 한 번에 목록에서 가져올 게시글 수
MAX_ARTICLES_TO_SCAN = 20

# Playwright 타임아웃 단축 (Render 512MB RAM 환경 최적화)
PAGE_TIMEOUT = 15000

# Gemini 모델 설정
CANDIDATE_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-1.5-flash"
]

GEMINI_TIMEOUT = 12.0


# ============================================================
# 2. Gemini Client
# ============================================================

client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None


# ============================================================
# Discord Bot 설정
# ============================================================

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)


# ============================================================
# 3. Flask - Render Health Check
# ============================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "Bot is running!", 200


def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


# ============================================================
# 4. 상태 저장
# ============================================================

def load_state():
    if not os.path.exists(STATE_FILE):
        return {
            "initialized": False,
            "seen_ids": []
        }

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            raise ValueError("상태 파일 형식 오류")

        data.setdefault("initialized", False)
        data.setdefault("seen_ids", [])

        return data

    except Exception as e:
        print(f"[WARN] 상태 파일 읽기 실패: {e}", flush=True)
        return {
            "initialized": False,
            "seen_ids": []
        }


def save_state(state):
    try:
        temp_file = STATE_FILE + ".tmp"
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(
                state,
                f,
                ensure_ascii=False,
                indent=2
            )
        os.replace(temp_file, STATE_FILE)
    except Exception as e:
        print(f"[WARN] 상태 파일 저장 실패: {e}", flush=True)


state = load_state()


# ============================================================
# 5. HTML / 문자열 처리
# ============================================================

def clean_text(text):
    if not text:
        return ""

    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)

    lines = []
    for line in text.split("\n"):
        line = line.strip()
        if line:
            lines.append(line)

    return "\n".join(lines).strip()


def clean_html(raw_html):
    if not raw_html:
        return ""

    text = re.sub(r"<script.*?</script>", " ", raw_html, flags=re.I | re.S)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</p\s*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)

    return clean_text(text)


# ============================================================
# 6. URL 처리
# ============================================================

def normalize_url(url):
    if not url:
        return None

    url = url.strip()
    if url.startswith("//"):
        return "https:" + url

    return urljoin(BASE_URL, url)


def extract_article_id(url):
    if not url:
        return None

    try:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)

        if "articleId" in query:
            return query["articleId"][0]
    except Exception:
        pass

    match = re.search(
        r"(?:articleId=|article/|articleId/)([A-Za-z0-9_-]+)",
        url,
        re.I
    )

    if match:
        return match.group(1)

    return None


# ============================================================
# 7. 이미지 URL 처리
# ============================================================

def choose_image_url(urls):
    if not urls:
        return None

    valid = []
    for url in urls:
        url = normalize_url(url)
        if not url:
            continue

        lower = url.lower()
        if lower.startswith("data:"):
            continue

        if any(x in lower for x in ["icon", "logo", "avatar", "sprite", "favicon"]):
            continue

        valid.append(url)

    if not valid:
        return None

    priority_words = ["og", "thumb", "thumbnail", "banner", "content", "image", "article"]

    for word in priority_words:
        for url in valid:
            if word in url.lower():
                return url

    return valid[0]


# ============================================================
# 8. 게시글 목록만 가져오기 (상세 진입 X)
# ============================================================

async def fetch_article_list(page):
    print("[INFO] 게시글 목록 수집 시작...", flush=True)
    try:
        # 1. ChatGPT 원래 방식대로 네트워크 통신이 완전히 끝날 때까지 대기
        await page.goto(BOARD_URL, wait_until="networkidle", timeout=PAGE_TIMEOUT)

        # 2. 게시판 렌더링 추가 안정화 대기 (1.5초)
        await page.wait_for_timeout(1500)

        # 3. ChatGPT 원래 코드처럼 게시글 링크(a 태그) 검색 범위 넓게 설정
        # (엔씨소프트 게시판 구조에 맞춘 broad selector)
        links = await page.query_selector_all("a[href*='/board/'], a[href*='articleId'], a[href*='view']")

        # 중복 링크 제거를 위한 집합
        seen_urls = set()
        articles = []

        for a in links:
            href = await a.get_attribute("href")
            if not href:
                continue

            full_url = href if href.startswith("http") else f"{BASE_URL}{href}"
            
            # 이미 처리한 동일 URL 스킵
            if full_url in seen_urls:
                continue

            # ID 추출 (articleId= 숫자 또는 /view/ 숫자 형태 모두 지원)
            match = re.search(r"(?:articleId=|/view/|/board/)([^&?/]+)", href)
            if match:
                art_id = match.group(1)
                text = (await a.inner_text()).strip()
                title = text.split("\n")[0] if text else "제목 없음"

                articles.append({
                    "id": art_id,
                    "title": title,
                    "url": full_url
                })
                seen_urls.add(full_url)

        print(f"[INFO] 게시글 {len(articles)}개 발견", flush=True)
        return articles

    except Exception as e:
        print(f"[ERROR] 게시글 목록 수집 실패: {e}", flush=True)
        return []


# ============================================================
# 9. 특정 게시글 1개 상세 크롤링
# ============================================================

async def fetch_article_detail(page, article):
    url = article["url"]
    print(f"[INFO] 상세 페이지 접속: {url}", flush=True)

    await page.goto(
        url,
        timeout=PAGE_TIMEOUT,
        wait_until="domcontentloaded"
    )

    try:
        await page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:
        pass

    # 제목 파싱
    title = ""
    title_selectors = ["h1", "h2", "[class*='title']", "[class*='Title']", ".board-title", ".article-title"]

    for selector in title_selectors:
        try:
            elements = await page.query_selector_all(selector)
            for el in elements:
                text = clean_text(await el.inner_text())
                if text and len(text) >= 2:
                    title = text
                    break
            if title:
                break
        except Exception:
            continue

    if not title:
        title = article.get("list_title", "아이온2 공지사항")

    # 본문 파싱
    content = ""
    content_selectors = [
        ".board-contents", ".article-body", ".article-content", ".contents",
        "[class*='board-content']", "[class*='article-content']",
        "[class*='article-body']", "[class*='viewer']", "[class*='content']",
        "article", "main"
    ]

    candidates = []
    for selector in content_selectors:
        try:
            elements = await page.query_selector_all(selector)
            for el in elements:
                try:
                    text = clean_text(await el.inner_text())
                except Exception:
                    continue

                if len(text) >= 30:
                    candidates.append(text)
        except Exception:
            continue

    if candidates:
        candidates.sort(key=lambda x: len(x))
        content = candidates[0]
        if len(content) < 100 and len(candidates) > 1:
            content = candidates[1]

    # 이미지 파싱
    image_candidates = []
    try:
        og_images = await page.query_selector_all("meta[property='og:image']")
        for el in og_images:
            value = await el.get_attribute("content")
            if value:
                image_candidates.append(value)
    except Exception:
        pass

    image_selectors = [
        ".board-contents img", ".article-body img", ".article-content img",
        ".contents img", "[class*='content'] img", "[class*='article'] img", "article img"
    ]

    for selector in image_selectors:
        try:
            images = await page.query_selector_all(selector)
            for img in images:
                src = await img.get_attribute("src")
                if src:
                    image_candidates.append(src)
                data_src = await img.get_attribute("data-src")
                if data_src:
                    image_candidates.append(data_src)
        except Exception:
            continue

    image_url = choose_image_url(image_candidates)

    print(
        f"[INFO] 상세 크롤링 완료 | 제목={title[:40]} | 본문={len(content)}자 | 이미지={'있음' if image_url else '없음'}",
        flush=True
    )

    return {
        "id": article["id"],
        "url": url,
        "title": title,
        "content": content,
        "image_url": image_url
    }


# ============================================================
# 10. 최신 공지 1개만 빠르게 가져오기 (!확인 용)
# ============================================================

async def fetch_latest_article():
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
            )
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800}
            )
            page = await context.new_page()

            articles = await fetch_article_list(page)
            if not articles:
                await browser.close()
                return None

            # 맨 위 최신글 1개만 상세 크롤링
            latest_detail = await fetch_article_detail(page, articles[0])
            await browser.close()
            return latest_detail

    except Exception as e:
        print(f"[ERROR] 최신 공지 크롤링 실패: {e}", flush=True)
        traceback.print_exc()
        return None


# ============================================================
# 11. Gemini 요약
# ============================================================

async def summarize_with_gemini(title, content):
    if not GEMINI_API_KEY or not client:
        print("[WARN] GEMINI_API_KEY가 설정되어 있지 않습니다.", flush=True)
        return None

    content = clean_html(content)
    if len(content) < 30:
        content = f"제목: {title}"

    content_for_ai = content[:6000]

    prompt = f"""
너는 아이온2 디스코드 공지사항 요약 봇이다.

아래 공지사항을 읽고 실제 유저가 게임을 하는 데 필요한 정보만 구체적으로 정리해라.

[출력 형식]

📌 **주요 업데이트 및 점검 내용**
- 점검 시간
- 신규 콘텐츠
- 핵심 업데이트
- 주요 변경사항

🛠 **추가 및 개선 사항**
- 시스템 변경
- 밸런스 변경
- 오류 수정
- 편의성 개선

🎁 **보상 및 이벤트 정보**
- 보상 내용
- 보상 수량
- 이벤트 기간
- 참여 방법
- 지급 조건

⚠️ **주의 및 안내 사항**
- 유저가 반드시 알아야 하는 사항
- 점검 전 준비사항
- 제한사항
- 기타 중요한 안내

규칙:
1. 공지에 실제로 존재하는 내용만 작성한다.
2. 공지에 없는 항목은 해당 항목 자체를 생략한다.
3. 시간, 날짜, 아이템명, 수량, 확률, 기간 등 구체적인 숫자는 가능한 한 그대로 표시한다.
4. 단순한 홍보 문구나 인사말은 제거한다.
5. 원문의 의미를 임의로 바꾸지 않는다.
6. 너무 짧게 요약하지 말고 중요한 내용은 충분히 포함한다.
7. 출력 형식 이외의 설명은 하지 않는다.

[공지 제목]
{title}

[공지 본문]
{content_for_ai}
"""

    start_time = time.time()
    loop = asyncio.get_running_loop()

    for model_name in CANDIDATE_MODELS:
        try:
            def call_api(m=model_name):
                return client.models.generate_content(
                    model=m,
                    contents=prompt
                )

            response = await asyncio.wait_for(
                loop.run_in_executor(None, call_api),
                timeout=GEMINI_TIMEOUT
            )

            if response and response.text:
                elapsed = time.time() - start_time
                print(f"[INFO] AI 요약 성공 (모델={model_name}, 소요={elapsed:.2f}초)", flush=True)
                return response.text.strip()

        except asyncio.TimeoutError:
            print(f"[WARN] {model_name} Timeout ({GEMINI_TIMEOUT}s)", flush=True)
        except Exception as e:
            print(f"[WARN] {model_name} 호출 실패: {e}", flush=True)

    print("[ERROR] 모든 Gemini 모델 호출 실패", flush=True)
    return None


# ============================================================
# 12. Discord Embed 생성
# ============================================================

def build_embed(article, summary):
    embed = discord.Embed(
        title=f"📢 {article['title']}",
        url=article["url"],
        color=0x00FF00
    )

    if summary:
        if len(summary) > 1020:
            summary = summary[:1017] + "..."
        embed.add_field(
            name="🤖 AI 주요 내용 요약",
            value=summary,
            inline=False
        )
    else:
        fallback = clean_html(article.get("content", ""))
        if not fallback:
            fallback = "공지 내용을 가져오지 못했습니다."
        if len(fallback) > 1020:
            fallback = fallback[:1017] + "..."
        embed.add_field(
            name="📝 공지 내용",
            value=fallback,
            inline=False
        )
        embed.set_footer(text="⚠️ AI 요약 생성 실패")

    embed.add_field(
        name="🔗 공지 바로가기",
        value=f"[공지사항 전체보기]({article['url']})",
        inline=False
    )

    if article.get("image_url"):
        embed.set_image(url=article["image_url"])

    return embed


# ============================================================
# 13. Discord 채널 중복 체크
# ============================================================

async def was_already_sent(channel, article_id):
    if not article_id:
        return False

    try:
        async for message in channel.history(limit=100):
            if not message.embeds:
                continue
            for embed in message.embeds:
                if embed.url and extract_article_id(embed.url) == article_id:
                    return True
    except Exception as e:
        print(f"[WARN] Discord 중복 확인 실패: {e}", flush=True)

    return False


# ============================================================
# 14. 공지 1개 전송 처리
# ============================================================

async def process_article(article, channel):
    article_id = article["id"]

    if await was_already_sent(channel, article_id):
        print(f"[SKIP] 이미 전송된 공지: {article_id}", flush=True)
        return False

    summary = await summarize_with_gemini(article["title"], article["content"])
    embed = build_embed(article, summary)

    await channel.send(embed=embed)
    print(f"[SEND] Discord 전송 완료: {article_id}", flush=True)
    return True


# ============================================================
# 15. 자동 공지 확인 핵심 함수 (Early Exit 적용)
# ============================================================

async def do_check_updates():
    if not CHANNEL_ID:
        print("[WARN] DISCORD_CHANNEL_ID가 설정되어 있지 않습니다.", flush=True)
        return

    try:
        channel_id = int(CHANNEL_ID)
    except ValueError:
        print("[ERROR] DISCORD_CHANNEL_ID가 숫자가 아닙니다.", flush=True)
        return

    channel = bot.get_channel(channel_id)
    if not channel:
        try:
            channel = await bot.fetch_channel(channel_id)
        except Exception as e:
            print(f"[ERROR] Discord 채널 조회 실패: {e}", flush=True)
            return

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800}
        )
        page = await context.new_page()

        # 1. 목록 수집 (상세 진입 X)
        articles_list = await fetch_article_list(page)
        if not articles_list:
            print("[WARN] 게시글 목록을 가져오지 못했습니다.", flush=True)
            await browser.close()
            return

        current_ids = [a["id"] for a in articles_list]

        # 2. 최초 실행 기준점 설정
        if not state["initialized"]:
            state["seen_ids"] = current_ids
            state["initialized"] = True
            save_state(state)
            print(f"[INFO] 최초 실행 기준점 저장: {len(current_ids)}개", flush=True)
            await browser.close()
            return

        # 3. 미전송 신규 공지 선별 (Early Exit: 이미 확인된 글 만나면 검색 중단)
        new_targets = []
        for art in articles_list:
            if art["id"] in state["seen_ids"] or await was_already_sent(channel, art["id"]):
                break
            new_targets.append(art)

        if not new_targets:
            print("[INFO] 새로운 공지가 없습니다.", flush=True)
            await browser.close()
            return

        print(f"[NEW] 새로운 공지 {len(new_targets)}개 발견! 상세 수집 시작...", flush=True)

        # 4. 신규 공지에 대해서만 과거 순(역순)으로 상세 크롤링 및 전송 (최대 3개 제한)
        for art in reversed(new_targets[:3]):
            try:
                detail = await fetch_article_detail(page, art)
                if detail:
                    sent = await process_article(detail, channel)
                    if sent or await was_already_sent(channel, detail["id"]):
                        if detail["id"] not in state["seen_ids"]:
                            state["seen_ids"].append(detail["id"])
                        state["seen_ids"] = state["seen_ids"][-100:]
                        save_state(state)
            except Exception as e:
                print(f"[ERROR] 공지 처리 실패 (ID={art['id']}): {e}", flush=True)
                traceback.print_exc()

        await browser.close()


# ============================================================
# 16. !확인
# ============================================================

@bot.command(name="확인")
async def check_command(ctx):
    status = await ctx.send("🔍 실제 홈페이지에서 최신 공지사항을 확인하는 중입니다...")

    try:
        article = await fetch_latest_article()

        if not article:
            await status.edit(content="❌ 공지사항을 불러오지 못했습니다.\nRender 로그를 확인해 주세요.")
            return

        await status.edit(content="🔍 최신 공지를 찾았습니다.\n🤖 AI 요약을 생성하는 중입니다...")

        summary = await summarize_with_gemini(article["title"], article["content"])
        embed = build_embed(article, summary)

        await status.delete()
        await ctx.send(embed=embed)

    except Exception as e:
        print(f"[ERROR] !확인 명령어 처리 중 에러 발생: {e}", flush=True)
        traceback.print_exc()
        await status.edit(content="❌ 처리 중 오류가 발생했습니다. Render 로그를 확인해 주세요.")


# ============================================================
# 17. !테스트알림
# ============================================================

@bot.command(name="테스트알림")
async def test_notification_command(ctx):
    await ctx.send("🧪 실제 홈페이지의 최신 공지를 이용해 테스트를 진행합니다...")

    if not CHANNEL_ID:
        await ctx.send("❌ DISCORD_CHANNEL_ID가 설정되어 있지 않습니다.")
        return

    try:
        channel = bot.get_channel(int(CHANNEL_ID))
        if not channel:
            channel = await bot.fetch_channel(int(CHANNEL_ID))

        article = await fetch_latest_article()
        if not article:
            await ctx.send("❌ 최신 공지를 가져오지 못했습니다.")
            return

        summary = await summarize_with_gemini(article["title"], article["content"])
        embed = build_embed(article, summary)

        await channel.send(embed=embed)
        await ctx.send("✅ 테스트 알림 전송 완료.")

    except Exception as e:
        print(f"[ERROR] 테스트알림 실패: {e}", flush=True)
        traceback.print_exc()
        await ctx.send(f"❌ 테스트알림 실패\n```text\n{e}\n```")


# ============================================================
# 18. 5분 자동 감지 루프
# ============================================================

@tasks.loop(minutes=5)
async def check_updates():
    print("[CHECK] 5분 주기 공지 확인 시작", flush=True)
    await do_check_updates()


@check_updates.before_loop
async def before_check_updates():
    await bot.wait_until_ready()
    print("[INFO] 5분 주기 자동 공지 감지 시작", flush=True)


# ============================================================
# 19. Discord 이벤트 및 예외 처리
# ============================================================

@bot.event
async def on_ready():
    print(f"[INFO] 디스코드 봇 로그인 성공: {bot.user.name}", flush=True)
    print(f"[INFO] 자동 감지 상태: {state.get('initialized', False)}", flush=True)

    if not check_updates.is_running():
        check_updates.start()


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return

    print(f"[ERROR] Discord 명령어 오류: {error}", flush=True)
    traceback.print_exc()

    try:
        await ctx.send(f"❌ 명령 실행 중 오류가 발생했습니다.\n`{error}`")
    except Exception:
        pass


# ============================================================
# 20. 메인 구동
# ============================================================

if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    if not DISCORD_TOKEN:
        print("[ERROR] DISCORD_TOKEN 환경변수가 없습니다.", flush=True)
        sys.exit(1)

    print("[INFO] Aion2 Update Bot 시작", flush=True)
    bot.run(DISCORD_TOKEN)

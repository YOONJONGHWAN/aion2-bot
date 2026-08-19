import os
import re
import json
import asyncio
import threading
import tempfile
from urllib.parse import urljoin, urlparse, parse_qs

import discord
from discord.ext import commands, tasks
from flask import Flask
from google import genai
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError


# ============================================================
# 0. 환경 설정
# ============================================================

BOARD_URL = "https://aion2.plaync.com/ko-kr/board/cm_story/list"
BASE_URL = "https://aion2.plaync.com"

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
CHANNEL_ID_RAW = os.getenv("DISCORD_CHANNEL_ID", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip().strip('"').strip("'")

# 사용자가 원했던 30분 주기
CHECK_INTERVAL_MINUTES = int(os.getenv("CHECK_INTERVAL_MINUTES", "30"))

# 한 번의 목록 조회에서 확인할 최대 공지 수.
# 자동 감지에서 너무 오래된 글까지 매번 상세 크롤링하지 않도록 제한한다.
MAX_ARTICLES_TO_SCAN = int(os.getenv("MAX_ARTICLES_TO_SCAN", "20"))

# 상태 파일. Render Persistent Disk를 연결했다면 이 경로를 환경변수로 지정할 수 있다.
DATA_FILE = os.getenv("PROCESSED_IDS_FILE", "processed_ids.json")

# Gemini 모델은 환경변수로 변경 가능.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash").strip()
GEMINI_FALLBACK_MODELS = [
    model.strip()
    for model in os.getenv(
        "GEMINI_FALLBACK_MODELS",
        "gemini-3.1-flash-lite,gemini-3.5-flash-lite"
    ).split(",")
    if model.strip()
]

# 상세 페이지 하나당 최대 대기 시간
DETAIL_TIMEOUT_MS = 30000

# Gemini 호출 최대 대기 시간
GEMINI_TIMEOUT_SECONDS = 15

# 동시에 여러 공지를 처리하지 않도록 자동 감지 락
check_lock = asyncio.Lock()

# Gemini 클라이언트
gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None


# ============================================================
# 1. 공지 처리 상태
# ============================================================

def load_processed_ids():
    if not os.path.exists(DATA_FILE):
        return set()

    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, list):
            return {str(x) for x in data}

        return set()

    except Exception as e:
        print(f"[WARN] 처리 상태 파일 읽기 실패: {e}", flush=True)
        return set()


def save_processed_ids(ids_set):
    """파일을 임시 파일에 먼저 저장한 뒤 교체하여 상태 파일 손상을 줄인다."""
    try:
        directory = os.path.dirname(os.path.abspath(DATA_FILE))
        os.makedirs(directory, exist_ok=True)

        fd, temp_path = tempfile.mkstemp(
            prefix=".processed_ids_",
            suffix=".tmp",
            dir=directory
        )

        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(
                    sorted(ids_set),
                    f,
                    ensure_ascii=False,
                    indent=2
                )
                f.flush()
                os.fsync(f.fileno())

            os.replace(temp_path, DATA_FILE)

        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    except Exception as e:
        print(f"[WARN] 처리 상태 저장 실패: {e}", flush=True)


processed_ids = load_processed_ids()


# ============================================================
# 2. Discord 설정
# ============================================================

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)


def get_channel():
    if not CHANNEL_ID_RAW:
        return None

    try:
        return bot.get_channel(int(CHANNEL_ID_RAW))
    except ValueError:
        print(
            f"[ERROR] DISCORD_CHANNEL_ID가 숫자가 아닙니다: {CHANNEL_ID_RAW}",
            flush=True
        )
        return None


# ============================================================
# 3. Flask / Render Keep-Alive
# ============================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "Aion2 Update Bot is active!", 200


def run_flask():
    port = int(os.environ.get("PORT", "10000"))
    app.run(
        host="0.0.0.0",
        port=port,
        use_reloader=False
    )


# ============================================================
# 4. HTML / URL 보조 함수
# ============================================================

def clean_text(text):
    if not text:
        return ""

    text = re.sub(r"\s+", " ", text)
    return text.strip()


def absolute_url(url):
    if not url:
        return None

    return urljoin(BASE_URL, url)


def extract_article_id(url):
    if not url:
        return None

    try:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        article_id = query.get("articleId", [None])[0]

        if article_id:
            return article_id

    except Exception:
        pass

    match = re.search(r"articleId=([A-Za-z0-9_-]+)", url)
    return match.group(1) if match else None


def normalize_image_url(url):
    if not url:
        return None

    url = url.strip()

    if not url:
        return None

    return absolute_url(url)


def first_non_empty(values):
    for value in values:
        if value and str(value).strip():
            return str(value).strip()
    return None


# ============================================================
# 5. 게시판 목록 크롤링
# ============================================================

async def collect_article_links(page):
    """
    동적 게시판에서 articleId 링크를 수집한다.
    실제 요소가 나타날 때까지 기다린 후 여러 링크를 수집한다.
    """

    await page.goto(
        BOARD_URL,
        timeout=DETAIL_TIMEOUT_MS,
        wait_until="domcontentloaded"
    )

    selector = "a[href*='articleId=']"

    try:
        await page.wait_for_selector(
            selector,
            timeout=20000
        )
    except PlaywrightTimeoutError:
        # DOMContentLoaded 직후 아직 JS 렌더링이 끝나지 않은 경우를 대비한다.
        await asyncio.sleep(3)

    # 페이지 JS 렌더링이 완료될 시간을 조금 더 준다.
    await asyncio.sleep(2)

    elements = await page.query_selector_all(selector)

    links = []
    seen = set()

    for element in elements:
        href = await element.get_attribute("href")
        if not href:
            continue

        full_url = absolute_url(href)
        article_id = extract_article_id(full_url)

        if not article_id or article_id in seen:
            continue

        seen.add(article_id)

        title = clean_text(await element.inner_text())

        links.append({
            "id": article_id,
            "url": full_url,
            "list_title": title
        })

        if len(links) >= MAX_ARTICLES_TO_SCAN:
            break

    print(
        f"[CRAWL] 게시판에서 유효 공지 링크 {len(links)}개 발견",
        flush=True
    )

    return links


# ============================================================
# 6. 상세 공지 크롤링
# ============================================================

async def extract_article_detail(page, link_data):
    article_id = link_data["id"]
    url = link_data["url"]

    await page.goto(
        url,
        timeout=DETAIL_TIMEOUT_MS,
        wait_until="domcontentloaded"
    )

    # 동적 본문/이미지 렌더링 대기
    await asyncio.sleep(2)

    # -------------------------
    # 제목
    # -------------------------
    title = first_non_empty([
        await get_inner_text(page, "h1"),
        await get_inner_text(page, "h2"),
        await get_inner_text(page, "[class*='board-title']"),
        await get_inner_text(page, "[class*='title']"),
        link_data.get("list_title")
    ]) or "아이온2 공지사항"

    # -------------------------
    # 본문
    # -------------------------
    content_selectors = [
        ".board-contents",
        "[class*='board-contents']",
        ".viewer",
        "[class*='viewer']",
        "[class*='article-content']",
        "[class*='article']",
        "article",
        "[class*='content']"
    ]

    content = ""

    # 후보 중 가장 긴 의미 있는 텍스트를 선택한다.
    candidates = []

    for selector in content_selectors:
        text = await get_inner_text(page, selector)

        if text and len(text) >= 30:
            candidates.append(text)

    if candidates:
        content = max(candidates, key=len)

    # 너무 긴 페이지 전체 텍스트가 잡힌 경우에도 최소한 공지 내용이
    # 포함되도록 가장 긴 후보를 사용한다.
    content = clean_text(content)

    # -------------------------
    # 이미지
    # -------------------------
    image_url = await extract_image_url(page)

    return {
        "id": article_id,
        "url": url,
        "title": title,
        "content": content,
        "image_url": image_url
    }


async def get_inner_text(page, selector):
    try:
        element = await page.query_selector(selector)

        if not element:
            return ""

        return clean_text(await element.inner_text())

    except Exception:
        return ""


async def extract_image_url(page):
    """
    실제 공지 대표 이미지를 찾기 위해 여러 방법을 순서대로 확인한다.
    """

    candidates = []

    # 1. OpenGraph
    for selector in [
        "meta[property='og:image']",
        "meta[property='og:image:url']",
        "meta[name='twitter:image']"
    ]:
        try:
            element = await page.query_selector(selector)
            if element:
                value = await element.get_attribute("content")
                if value:
                    candidates.append(value)
        except Exception:
            pass

    # 2. 본문 이미지
    image_selectors = [
        ".board-contents img",
        "[class*='board-contents'] img",
        ".viewer img",
        "[class*='viewer'] img",
        "[class*='article-content'] img",
        "article img",
        "[class*='content'] img"
    ]

    for selector in image_selectors:
        try:
            elements = await page.query_selector_all(selector)

            for element in elements[:10]:
                for attr in [
                    "src",
                    "data-src",
                    "data-original",
                    "data-lazy-src",
                    "data-image"
                ]:
                    value = await element.get_attribute(attr)
                    if value:
                        candidates.append(value)

                srcset = await element.get_attribute("srcset")
                if srcset:
                    # srcset의 마지막 후보를 사용
                    last_candidate = srcset.split(",")[-1].strip().split(" ")[0]
                    if last_candidate:
                        candidates.append(last_candidate)

        except Exception:
            pass

    # 빈 값 / data URL 제거
    for candidate in candidates:
        candidate = candidate.strip()

        if not candidate:
            continue

        if candidate.startswith("data:"):
            continue

        return normalize_image_url(candidate)

    return None


# ============================================================
# 7. 여러 공지 크롤링
# ============================================================

async def fetch_articles(limit=None):
    """
    목록에서 여러 공지를 찾고 각 상세 페이지를 크롤링한다.

    반환 순서:
    오래된 공지 -> 최신 공지

    자동 감지 시에는 processed_ids에 없는 공지만 최종적으로 처리한다.
    """

    if limit is None:
        limit = MAX_ARTICLES_TO_SCAN

    articles = []

    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-blink-features=AutomationControlled"
                ]
            )

            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/123.0.0.0 Safari/537.36"
                ),
                viewport={
                    "width": 1280,
                    "height": 800
                },
                locale="ko-KR"
            )

            page = await context.new_page()

            links = await collect_article_links(page)

            if limit:
                links = links[:limit]

            # 목록은 최신순이라고 가정한다.
            # 처리할 때는 오래된 것부터 처리한다.
            for link_data in reversed(links):
                try:
                    article = await extract_article_detail(
                        page,
                        link_data
                    )

                    articles.append(article)

                    print(
                        f"[CRAWL] {article['title']} "
                        f"(ID: {article['id']}, "
                        f"image={'YES' if article['image_url'] else 'NO'}, "
                        f"content={len(article['content'])}자)",
                        flush=True
                    )

                except Exception as e:
                    print(
                        f"[WARN] 게시글 상세 크롤링 실패 "
                        f"(ID: {link_data['id']}): {e}",
                        flush=True
                    )

            await context.close()
            await browser.close()

    except Exception as e:
        print(
            f"[ERROR] Playwright 크롤링 실패: {e}",
            flush=True
        )

    return articles


# ============================================================
# 8. Gemini 요약
# ============================================================

async def summarize_with_gemini(title, content):
    if not gemini_client:
        print("[WARN] GEMINI_API_KEY가 없습니다.", flush=True)
        return None

    text = clean_text(content)

    if len(text) < 30:
        text = f"공지 제목: {title}"

    # 너무 길 경우 앞부분만 보내되, 과도하게 짧게 자르지 않는다.
    text = text[:8000]

    prompt = f"""
너는 아이온2 디스코드 공지 요약 봇이다.

아래 공식 공지사항을 읽고 실제 내용에 근거해서 핵심만 정리해라.

[공지 제목]
{title}

[공지 내용]
{text}

[출력 규칙]
- 공지에 실제로 있는 내용만 작성한다.
- 추측하거나 내용을 만들어내지 않는다.
- 해당되는 항목만 출력하고 없는 항목은 생략한다.
- 군더더기 인사말은 쓰지 않는다.
- 너무 긴 문장은 짧고 명확하게 정리한다.

가능하면 아래 형식을 사용한다.

📌 **주요 업데이트 및 점검 내용**
- ...

🛠 **추가 및 개선 사항**
- ...

🎁 **이벤트 및 보상**
- ...

⚠️ **주의사항**
- ...

기간, 시간, 보상 수량, 변경되는 수치 등 중요한 숫자는 가능한 한 유지한다.
"""

    models = []
    for model in [GEMINI_MODEL, *GEMINI_FALLBACK_MODELS]:
        if model and model not in models:
            models.append(model)

    loop = asyncio.get_running_loop()

    for model_name in models:
        try:
            def call_api():
                return gemini_client.models.generate_content(
                    model=model_name,
                    contents=prompt
                )

            response = await asyncio.wait_for(
                loop.run_in_executor(None, call_api),
                timeout=GEMINI_TIMEOUT_SECONDS
            )

            if response and getattr(response, "text", None):
                print(
                    f"[AI] Gemini 요약 성공: {model_name}",
                    flush=True
                )
                return response.text.strip()

        except Exception as e:
            print(
                f"[WARN] Gemini {model_name} 실패: {e}",
                flush=True
            )

    print("[WARN] Gemini 모든 모델 호출 실패", flush=True)
    return None


# ============================================================
# 9. Discord Embed
# ============================================================

def create_article_embed(article, summary):
    embed = discord.Embed(
        title=f"📢 {article['title'][:256]}",
        url=article["url"],
        color=0x00AEEF
    )

    if summary:
        summary_text = summary.strip()

        # Discord field value 최대 1024자
        if len(summary_text) > 1000:
            summary_text = summary_text[:997] + "..."

        embed.add_field(
            name="🤖 AI 주요 내용 요약",
            value=summary_text,
            inline=False
        )

    else:
        fallback = (
            clean_text(article.get("content", ""))[:900]
            or "공지 세부 내용을 확인하세요."
        )

        embed.add_field(
            name="📝 공지 내용 미리보기",
            value=fallback,
            inline=False
        )

    embed.add_field(
        name="🔗 공지 바로가기",
        value=f"[공지사항 전체보기]({article['url']})",
        inline=False
    )

    if article.get("image_url"):
        try:
            embed.set_image(url=article["image_url"])
        except Exception:
            pass

    return embed


# ============================================================
# 10. Discord 명령어
# ============================================================

@bot.command(name="확인")
async def check_command(ctx):
    """
    실제 홈페이지의 최신 공지를 가져온다.
    processed_ids에는 영향을 주지 않는다.
    """

    await ctx.send(
        "🔍 아이온2 홈페이지에서 최신 공지를 확인하는 중입니다..."
    )

    try:
        articles = await fetch_articles(limit=1)

        if not articles:
            await ctx.send(
                "❌ 공지사항을 불러오지 못했습니다."
            )
            return

        article = articles[-1]

        summary = await summarize_with_gemini(
            article["title"],
            article["content"]
        )

        embed = create_article_embed(
            article,
            summary
        )

        await ctx.send(embed=embed)

    except Exception as e:
        print(
            f"[ERROR] !확인 처리 실패: {e}",
            flush=True
        )
        await ctx.send(
            "❌ 최신 공지 확인 중 오류가 발생했습니다."
        )


@bot.command(name="테스트알림")
async def test_notification_command(ctx):
    """
    실제 최신 공지를 가져와 자동 알림과 동일한 Embed를
    지정된 알림 채널에 전송한다.

    이 명령은 processed_ids를 변경하지 않는다.
    """

    channel = get_channel()

    if not channel:
        await ctx.send(
            "❌ DISCORD_CHANNEL_ID가 올바르게 설정되지 않았습니다."
        )
        return

    await ctx.send(
        "🧪 실제 최신 공지로 테스트 알림을 전송합니다..."
    )

    try:
        articles = await fetch_articles(limit=1)

        if not articles:
            await ctx.send(
                "❌ 테스트용 공지사항 수집에 실패했습니다."
            )
            return

        article = articles[-1]

        summary = await summarize_with_gemini(
            article["title"],
            article["content"]
        )

        embed = create_article_embed(
            article,
            summary
        )

        await channel.send(embed=embed)

        await ctx.send(
            "✅ 테스트 알림 전송이 완료되었습니다."
        )

    except Exception as e:
        print(
            f"[ERROR] !테스트알림 처리 실패: {e}",
            flush=True
        )
        await ctx.send(
            "❌ 테스트 알림 전송 중 오류가 발생했습니다."
        )


@bot.command(name="상태")
async def status_command(ctx):
    """봇 상태와 처리된 공지 수를 확인한다."""

    channel = get_channel()

    await ctx.send(
        "📊 상태\n"
        f"- 자동 감지 주기: {CHECK_INTERVAL_MINUTES}분\n"
        f"- 처리된 공지 ID: {len(processed_ids)}개\n"
        f"- 알림 채널 설정: {'정상' if channel else '확인 필요'}\n"
        f"- Gemini: {'설정됨' if gemini_client else '미설정'}"
    )


# ============================================================
# 11. 자동 감지
# ============================================================

async def do_check_updates():
    global processed_ids

    channel = get_channel()

    if not channel:
        print(
            "[WARN] 알림 채널을 찾을 수 없습니다.",
            flush=True
        )
        return

    # 동시에 두 번 실행되는 것을 방지
    if check_lock.locked():
        print(
            "[WARN] 이전 자동 감지가 아직 진행 중이므로 이번 검사를 건너뜁니다.",
            flush=True
        )
        return

    async with check_lock:
        print(
            "[CHECK] 공지 확인 시작",
            flush=True
        )

        articles = await fetch_articles(
            limit=MAX_ARTICLES_TO_SCAN
        )

        if not articles:
            print(
                "[WARN] 공지를 하나도 가져오지 못했습니다.",
                flush=True
            )
            return

        # --------------------------------------------
        # 최초 실행
        # --------------------------------------------
        if not processed_ids:
            for article in articles:
                processed_ids.add(article["id"])

            save_processed_ids(processed_ids)

            print(
                f"[INFO] 최초 기준점 설정 완료: "
                f"{len(articles)}개 공지 등록",
                flush=True
            )
            return

        # --------------------------------------------
        # 신규 공지 찾기
        # --------------------------------------------
        new_articles = [
            article
            for article in articles
            if article["id"] not in processed_ids
        ]

        if not new_articles:
            print(
                f"[CHECK] 신규 공지 없음 "
                f"(확인 {len(articles)}개)",
                flush=True
            )
            return

        print(
            f"[NEW] 신규 공지 {len(new_articles)}개 발견",
            flush=True
        )

        # 오래된 공지부터 순서대로 처리
        for article in new_articles:
            try:
                summary = await summarize_with_gemini(
                    article["title"],
                    article["content"]
                )

                embed = create_article_embed(
                    article,
                    summary
                )

                await channel.send(embed=embed)

                # 실제 Discord 전송 성공 후에만 processed 처리
                processed_ids.add(article["id"])
                save_processed_ids(processed_ids)

                print(
                    f"[SUCCESS] 신규 공지 전송 완료: "
                    f"{article['title']}",
                    flush=True
                )

                # 연속 전송 간 약간의 간격
                await asyncio.sleep(2)

            except Exception as e:
                # 실패한 공지는 processed_ids에 넣지 않는다.
                # 다음 주기에 다시 시도할 수 있다.
                print(
                    f"[ERROR] 신규 공지 처리 실패 "
                    f"(ID: {article['id']}): {e}",
                    flush=True
                )


@tasks.loop(minutes=CHECK_INTERVAL_MINUTES)
async def check_updates():
    try:
        await do_check_updates()
    except Exception as e:
        print(
            f"[ERROR] 자동 감지 루프 오류: {e}",
            flush=True
        )


@check_updates.before_loop
async def before_check_updates():
    await bot.wait_until_ready()


# ============================================================
# 12. Discord 이벤트
# ============================================================

@bot.event
async def on_ready():
    print(
        f"[INFO] 디스코드 봇 로그인 성공: {bot.user}",
        flush=True
    )

    print(
        f"[INFO] 자동 감지 주기: {CHECK_INTERVAL_MINUTES}분",
        flush=True
    )

    print(
        f"[INFO] 처리된 공지 ID: {len(processed_ids)}개",
        flush=True
    )

    if not check_updates.is_running():
        check_updates.start()
        print(
            "[INFO] 자동 공지 감지 시작",
            flush=True
        )


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return

    print(
        f"[ERROR] Discord 명령어 오류: {error}",
        flush=True
    )


# ============================================================
# 13. 메인
# ============================================================

if __name__ == "__main__":
    print("[INFO] Aion2 Update Bot 시작", flush=True)

    flask_thread = threading.Thread(
        target=run_flask,
        daemon=True
    )
    flask_thread.start()

    if not DISCORD_TOKEN:
        print(
            "[ERROR] DISCORD_TOKEN 환경변수가 없습니다.",
            flush=True
        )
    else:
        bot.run(DISCORD_TOKEN)

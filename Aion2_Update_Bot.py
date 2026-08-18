import os
import re
import json
import time
import asyncio
import threading
import subprocess
import sys
from urllib.parse import urljoin, urlparse, parse_qs

import discord
from discord.ext import commands, tasks
from flask import Flask
from google import genai
from playwright.async_api import async_playwright


# ============================================================
# 1. 기본 설정
# ============================================================

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = os.getenv("DISCORD_CHANNEL_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip().strip('"').strip("'")

BOARD_URL = "https://aion2.plaync.com/ko-kr/board/cm_story/list"
BASE_URL = "https://aion2.plaync.com"

# Render 재시작 시에도 파일 자체는 초기화될 수 있으므로
# Discord 채널 기록도 함께 중복 확인에 사용한다.
STATE_FILE = "aion2_state.json"

# 한 번에 확인할 게시글 수
MAX_ARTICLES_TO_SCAN = 20

# Playwright
PAGE_TIMEOUT = 25000

# Gemini 모델
CANDIDATE_MODELS = [
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash-lite",
]

GEMINI_TIMEOUT = 12.0


# ============================================================
# 2. Gemini
# ============================================================

client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None


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
    """
    여러 이미지 중 가장 가능성이 높은 이미지를 선택한다.
    """

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

        if any(
            x in lower
            for x in [
                "icon",
                "logo",
                "avatar",
                "sprite",
                "favicon"
            ]
        ):
            continue

        valid.append(url)

    if not valid:
        return None

    # 큰 이미지/대표 이미지일 가능성이 높은 순으로 판단
    priority_words = [
        "og",
        "thumb",
        "thumbnail",
        "banner",
        "content",
        "image",
        "article"
    ]

    for word in priority_words:
        for url in valid:
            if word in url.lower():
                return url

    return valid[0]


# ============================================================
# 8. Playwright로 게시글 목록 가져오기
# ============================================================

async def fetch_article_list(page):
    print("[INFO] 게시글 목록 수집 시작...", flush=True)

    await page.goto(
        BOARD_URL,
        timeout=PAGE_TIMEOUT,
        wait_until="domcontentloaded"
    )

    # JS 렌더링 대기
    try:
        await page.wait_for_load_state(
            "networkidle",
            timeout=10000
        )
    except Exception:
        pass

    await asyncio.sleep(1.5)

    links = await page.query_selector_all(
        "a[href*='articleId=']"
    )

    articles = []
    seen_ids = set()

    for link in links:
        try:
            href = await link.get_attribute("href")

            if not href:
                continue

            url = normalize_url(href)
            article_id = extract_article_id(url)

            if not article_id:
                continue

            if article_id in seen_ids:
                continue

            seen_ids.add(article_id)

            try:
                title = clean_text(
                    await link.inner_text()
                )
            except Exception:
                title = ""

            articles.append({
                "id": article_id,
                "url": url,
                "list_title": title
            })

            if len(articles) >= MAX_ARTICLES_TO_SCAN:
                break

        except Exception as e:
            print(
                f"[WARN] 게시글 링크 처리 실패: {e}",
                flush=True
            )

    print(
        f"[INFO] 게시글 {len(articles)}개 발견",
        flush=True
    )

    return articles


# ============================================================
# 9. 게시글 상세 페이지 크롤링
# ============================================================

async def fetch_article_detail(page, article):
    url = article["url"]

    print(
        f"[INFO] 상세 페이지 접속: {url}",
        flush=True
    )

    await page.goto(
        url,
        timeout=PAGE_TIMEOUT,
        wait_until="domcontentloaded"
    )

    try:
        await page.wait_for_load_state(
            "networkidle",
            timeout=10000
        )
    except Exception:
        pass

    # 동적 렌더링 안정화
    await asyncio.sleep(1.5)

    # --------------------------------------------------------
    # 제목
    # --------------------------------------------------------

    title = ""

    title_selectors = [
        "h1",
        "h2",
        "[class*='title']",
        "[class*='Title']",
        ".board-title",
        ".article-title"
    ]

    for selector in title_selectors:

        try:
            elements = await page.query_selector_all(selector)

            for el in elements:
                text = clean_text(
                    await el.inner_text()
                )

                if text and len(text) >= 2:
                    title = text
                    break

            if title:
                break

        except Exception:
            continue

    if not title:
        title = article.get(
            "list_title",
            "아이온2 공지사항"
        )


    # --------------------------------------------------------
    # 본문
    # --------------------------------------------------------

    content = ""

    content_selectors = [
        ".board-contents",
        ".article-body",
        ".article-content",
        ".contents",
        "[class*='board-content']",
        "[class*='article-content']",
        "[class*='article-body']",
        "[class*='viewer']",
        "[class*='content']",
        "article",
        "main"
    ]

    candidates = []

    for selector in content_selectors:

        try:
            elements = await page.query_selector_all(selector)

            for el in elements:

                try:
                    text = clean_text(
                        await el.inner_text()
                    )
                except Exception:
                    continue

                if len(text) >= 30:
                    candidates.append(text)

        except Exception:
            continue

    if candidates:
        # 너무 상위 컨테이너(main 등)가 잡히는 것을 방지하기 위해
        # 적당한 길이의 가장 구체적인 본문을 우선
        candidates.sort(
            key=lambda x: len(x)
        )

        content = candidates[0]

        # 너무 짧은 경우 조금 더 긴 후보 사용
        if len(content) < 100 and len(candidates) > 1:
            content = candidates[1]

    # --------------------------------------------------------
    # 이미지
    # --------------------------------------------------------

    image_candidates = []

    # 1. OG 이미지
    try:
        og_images = await page.query_selector_all(
            "meta[property='og:image']"
        )

        for el in og_images:
            value = await el.get_attribute("content")

            if value:
                image_candidates.append(value)

    except Exception:
        pass

    # 2. 본문 이미지
    image_selectors = [
        ".board-contents img",
        ".article-body img",
        ".article-content img",
        ".contents img",
        "[class*='content'] img",
        "[class*='article'] img",
        "article img"
    ]

    for selector in image_selectors:

        try:
            images = await page.query_selector_all(
                selector
            )

            for img in images:

                src = await img.get_attribute("src")

                if src:
                    image_candidates.append(src)

                data_src = await img.get_attribute(
                    "data-src"
                )

                if data_src:
                    image_candidates.append(data_src)

                srcset = await img.get_attribute(
                    "srcset"
                )

                if srcset:
                    for part in srcset.split(","):
                        candidate = part.strip().split(" ")[0]

                        if candidate:
                            image_candidates.append(candidate)

        except Exception:
            continue

    image_url = choose_image_url(
        image_candidates
    )

    print(
        f"[INFO] 상세 크롤링 완료 | "
        f"제목={title[:60]} | "
        f"본문={len(content)}자 | "
        f"이미지={'있음' if image_url else '없음'}",
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
# 10. 전체 게시글 수집
# ============================================================

async def fetch_articles():
    try:

        async with async_playwright() as p:

            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu"
                ]
            )

            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 "
                    "(Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 "
                    "(KHTML, like Gecko) "
                    "Chrome/123.0.0.0 Safari/537.36"
                ),
                viewport={
                    "width": 1280,
                    "height": 800
                }
            )

            page = await context.new_page()

            articles = await fetch_article_list(page)

            results = []

            for article in articles:

                try:
                    detail = await fetch_article_detail(
                        page,
                        article
                    )

                    if detail:
                        results.append(detail)

                except Exception as e:

                    print(
                        f"[WARN] 상세 공지 처리 실패 "
                        f"(ID={article['id']}): {e}",
                        flush=True
                    )

            await browser.close()

            return results

    except Exception as e:

        print(
            f"[ERROR] Playwright 크롤링 실패: {e}",
            flush=True
        )

        return []


# ============================================================
# 11. 최신 공지 1개 가져오기
# ============================================================

async def fetch_latest_article():
    articles = await fetch_articles()

    if not articles:
        return None

    return articles[0]


# ============================================================
# 12. Gemini 요약
# ============================================================

async def summarize_with_gemini(title, content):

    if not GEMINI_API_KEY or not client:
        print(
            "[WARN] GEMINI_API_KEY가 없습니다.",
            flush=True
        )
        return None

    content = clean_html(content)

    if len(content) < 30:
        content = f"제목: {title}"

    # 너무 짧게 잘라 중요한 내용을 잃지 않도록
    # 6000자까지 전달
    content_for_ai = content[:6000]

    prompt = f"""
너는 아이온2 디스코드 공지사항 요약 봇이다.

아래 공지사항을 읽고 실제 유저가 게임을 하는 데 필요한 정보만
구체적으로 정리해라.

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
5. 같은 내용을 반복하지 않는다.
6. 원문의 의미를 임의로 바꾸지 않는다.
7. 너무 짧게 요약하지 말고 중요한 내용은 충분히 포함한다.
8. 제목만 보고 내용을 추측하지 않는다.
9. 출력 형식 이외의 설명은 하지 않는다.

[공지 제목]
{title}

[공지 본문]
{content_for_ai}
"""

    start_time = time.time()

    loop = asyncio.get_running_loop()

    for model_name in CANDIDATE_MODELS:

        try:

            def call_api(model=model_name):
                return client.models.generate_content(
                    model=model,
                    contents=prompt
                )

            response = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    call_api
                ),
                timeout=GEMINI_TIMEOUT
            )

            if response and response.text:

                elapsed = time.time() - start_time

                print(
                    f"[INFO] AI 요약 성공 "
                    f"(모델={model_name}, "
                    f"소요={elapsed:.2f}초)",
                    flush=True
                )

                return response.text.strip()

        except asyncio.TimeoutError:

            print(
                f"[WARN] {model_name} "
                f"{GEMINI_TIMEOUT}초 timeout",
                flush=True
            )

        except Exception as e:

            print(
                f"[WARN] {model_name} 호출 실패: {e}",
                flush=True
            )

    print(
        "[ERROR] 모든 Gemini 모델 호출 실패",
        flush=True
    )

    return None


# ============================================================
# 13. Discord Embed 생성
# ============================================================

def build_embed(article, summary):

    embed = discord.Embed(
        title=f"📢 {article['title']}",
        url=article["url"],
        color=0x00FF00
    )

    if summary:

        # Discord field 최대 길이 방어
        if len(summary) > 1020:
            summary = summary[:1017] + "..."

        embed.add_field(
            name="🤖 AI 주요 내용 요약",
            value=summary,
            inline=False
        )

    else:

        fallback = clean_html(
            article.get("content", "")
        )

        if not fallback:
            fallback = "공지 내용을 가져오지 못했습니다."

        if len(fallback) > 1020:
            fallback = fallback[:1017] + "..."

        embed.add_field(
            name="📝 공지 내용",
            value=fallback,
            inline=False
        )

        embed.set_footer(
            text="⚠️ AI 요약 생성 실패"
        )

    embed.add_field(
        name="🔗 공지 바로가기",
        value=f"[공지사항 전체보기]({article['url']})",
        inline=False
    )

    image_url = article.get("image_url")

    if image_url:
        embed.set_image(url=image_url)

    return embed


# ============================================================
# 14. Discord 채널에서 이미 보낸 공지인지 확인
# ============================================================

async def was_already_sent(channel, article_id):

    if not article_id:
        return False

    try:

        async for message in channel.history(limit=100):

            if not message.embeds:
                continue

            for embed in message.embeds:

                url = embed.url

                if not url:
                    continue

                if extract_article_id(url) == article_id:
                    return True

    except Exception as e:

        print(
            f"[WARN] Discord 중복 확인 실패: {e}",
            flush=True
        )

    return False


# ============================================================
# 15. 공지 1개 처리
# ============================================================

async def process_article(article, channel):

    article_id = article["id"]

    print(
        f"[PROCESS] 공지 처리 시작: {article_id}",
        flush=True
    )

    # 이미 Discord에 보낸 공지면 중복 방지
    if await was_already_sent(
        channel,
        article_id
    ):

        print(
            f"[SKIP] 이미 전송된 공지: {article_id}",
            flush=True
        )

        return False

    summary = await summarize_with_gemini(
        article["title"],
        article["content"]
    )

    embed = build_embed(
        article,
        summary
    )

    await channel.send(
        embed=embed
    )

    print(
        f"[SEND] Discord 전송 완료: {article_id}",
        flush=True
    )

    return True


# ============================================================
# 16. 자동 공지 확인 핵심 함수
# ============================================================

async def do_check_updates(force=False):

    if not CHANNEL_ID:

        print(
            "[WARN] DISCORD_CHANNEL_ID가 없습니다.",
            flush=True
        )

        return

    try:
        channel_id = int(CHANNEL_ID)

    except ValueError:

        print(
            "[ERROR] DISCORD_CHANNEL_ID가 숫자가 아닙니다.",
            flush=True
        )

        return

    channel = bot.get_channel(channel_id)

    if not channel:

        try:
            channel = await bot.fetch_channel(
                channel_id
            )

        except Exception as e:

            print(
                f"[ERROR] Discord 채널 조회 실패: {e}",
                flush=True
            )

            return

    articles = await fetch_articles()

    if not articles:

        print(
            "[WARN] 공지를 하나도 가져오지 못했습니다.",
            flush=True
        )

        return

    current_ids = [
        article["id"]
        for article in articles
    ]

    # --------------------------------------------------------
    # 최초 실행
    # --------------------------------------------------------

    if not state["initialized"]:

        state["seen_ids"] = current_ids
        state["initialized"] = True

        save_state(state)

        print(
            f"[INFO] 최초 실행 기준점 저장: "
            f"{len(current_ids)}개",
            flush=True
        )

        return

    # --------------------------------------------------------
    # 새로운 공지 찾기
    # --------------------------------------------------------

    new_articles = []

    for article in reversed(articles):

        if article["id"] not in state["seen_ids"]:

            new_articles.append(article)

    if not new_articles:

        print(
            "[INFO] 새로운 공지가 없습니다.",
            flush=True
        )

        return

    print(
        f"[NEW] 새로운 공지 {len(new_articles)}개 발견",
        flush=True
    )

    # --------------------------------------------------------
    # 여러 공지를 각각 처리
    # --------------------------------------------------------

    for article in new_articles:

        try:

            sent = await process_article(
                article,
                channel
            )

            # 전송 성공했거나 이미 전송된 경우
            # seen에 등록
            if sent or await was_already_sent(
                channel,
                article["id"]
            ):

                state["seen_ids"].append(
                    article["id"]
                )

                # 최근 100개만 유지
                state["seen_ids"] = (
                    state["seen_ids"][-100:]
                )

                save_state(state)

        except Exception as e:

            print(
                f"[ERROR] 공지 처리 실패 "
                f"(ID={article['id']}): {e}",
                flush=True
            )

            # 한 공지가 실패해도
            # 다음 공지는 계속 처리
            continue


# ============================================================
# 17. !확인
# ============================================================

@bot.command(name="확인")
async def check_command(ctx):

    status = await ctx.send(
        "🔍 실제 홈페이지에서 최신 공지사항을 확인하는 중입니다..."
    )

    article = await fetch_latest_article()

    if not article:

        await status.edit(
            content=(
                "❌ 공지사항을 불러오지 못했습니다.\n"
                "Render 로그를 확인해 주세요."
            )
        )

        return

    await status.edit(
        content=(
            "🔍 최신 공지를 찾았습니다.\n"
            "🤖 AI 요약을 생성하는 중입니다..."
        )
    )

    summary = await summarize_with_gemini(
        article["title"],
        article["content"]
    )

    embed = build_embed(
        article,
        summary
    )

    await status.delete()

    await ctx.send(
        embed=embed
    )


# ============================================================
# 18. !테스트알림
# ============================================================

@bot.command(name="테스트알림")
async def test_notification_command(ctx):

    await ctx.send(
        "🧪 실제 홈페이지의 최신 공지를 이용해 "
        "자동 알림 전체 과정을 테스트합니다..."
    )

    if not CHANNEL_ID:

        await ctx.send(
            "❌ DISCORD_CHANNEL_ID가 설정되어 있지 않습니다."
        )

        return

    try:

        channel = bot.get_channel(
            int(CHANNEL_ID)
        )

        if not channel:

            channel = await bot.fetch_channel(
                int(CHANNEL_ID)
            )

        article = await fetch_latest_article()

        if not article:

            await ctx.send(
                "❌ 실제 홈페이지에서 최신 공지를 가져오지 못했습니다."
            )

            return

        print(
            f"[TEST] 테스트 공지: {article['id']}",
            flush=True
        )

        summary = await summarize_with_gemini(
            article["title"],
            article["content"]
        )

        embed = build_embed(
            article,
            summary
        )

        # 실제 자동 알림과 동일한 지정 채널에 전송
        await channel.send(
            embed=embed
        )

        await ctx.send(
            "✅ 테스트 알림 전송 완료.\n"
            "실제 홈페이지 → 크롤링 → AI 요약 → "
            "썸네일 → 상세 URL → 지정 채널 전송까지 확인되었습니다."
        )

    except Exception as e:

        print(
            f"[ERROR] 테스트알림 실패: {e}",
            flush=True
        )

        await ctx.send(
            f"❌ 테스트알림 실패\n```text\n{e}\n```"
        )


# ============================================================
# 19. 5분 자동 감지
# ============================================================

@tasks.loop(minutes=5)
async def check_updates():

    print(
        "[CHECK] 5분 주기 공지 확인 시작",
        flush=True
    )

    await do_check_updates()


@check_updates.before_loop
async def before_check_updates():

    await bot.wait_until_ready()

    print(
        "[INFO] 5분 주기 자동 공지 감지 시작",
        flush=True
    )


# ============================================================
# 20. Discord 로그인
# ============================================================

@bot.event
async def on_ready():

    print(
        f"[INFO] 디스코드 봇 로그인 성공: "
        f"{bot.user.name}",
        flush=True
    )

    print(
        f"[INFO] 자동 감지 상태: "
        f"{state.get('initialized', False)}",
        flush=True
    )

    if not check_updates.is_running():

        check_updates.start()


# ============================================================
# 21. 예외 처리
# ============================================================

@bot.event
async def on_command_error(ctx, error):

    if isinstance(
        error,
        commands.CommandNotFound
    ):
        return

    print(
        f"[ERROR] Discord 명령어 오류: {error}",
        flush=True
    )

    try:

        await ctx.send(
            f"❌ 명령 실행 중 오류가 발생했습니다.\n"
            f"`{error}`"
        )

    except Exception:
        pass


# ============================================================
# 22. 메인
# ============================================================

if __name__ == "__main__":

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

        sys.exit(1)

    print(
        "[INFO] Aion2 Update Bot 시작",
        flush=True
    )

    bot.run(DISCORD_TOKEN)

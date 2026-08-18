import asyncio
import logging
import os
from flask import Flask
from threading import Thread
import discord
from discord.ext import commands, tasks
import httpx
from google import genai
from google.genai import types
from playwright.async_api import async_playwright

# 로깅 설정
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# 환경 변수 로드
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Flask 앱 설정 (Render 포트 바인딩용)
app = Flask(__name__)

@app.route('/')
def home():
    return "Aion2 Update Bot is running!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# 디스코드 봇 설정
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# 목표 웹사이트 URL
TARGET_URL = "https://aion2.plaync.com/ko-kr/board/cm_story_list"
DETAIL_TIMEOUT = 60000  # 타임아웃 60초로 연장

# 전역 상태 변수
known_notices = set()

async def fetch_article_images(page, url):
    image_urls = []
    try:
        await page.goto(url, timeout=DETAIL_TIMEOUT, wait_until="domcontentloaded")
        await page.wait_for_timeout(4000)
        
        img_elements = await page.query_selector_all("img")
        for img in img_elements:
            src = await img.get_attribute("src")
            if src:
                if src.startswith("//"):
                    src = "https:" + src
                elif src.startswith("/"):
                    src = "https://aion2.plaync.com" + src
                
                src_lower = src.lower()
                exclude_keywords = ["logo", "icon", "avatar", "banner", "profile", "footer", "grade", "age"]
                if "http" in src and not any(kw in src_lower for kw in exclude_keywords):
                    if src not in image_urls:
                        image_urls.append(src)
    except Exception as e:
        logging.warning(f"이미지 수집 실패 ({url}): {e}")
    return image_urls

async def generate_ai_summary_from_images(title, image_urls):
    if not GEMINI_API_KEY or not image_urls:
        return f"새로운 공지사항이 등록되었습니다. (제목: {title})"
    
    client = genai.Client(api_key=GEMINI_API_KEY)
    contents = [
        f"게임 '아이온2'의 공식 공지사항 이미지입니다. 제목: {title}\n첨부된 이미지들을 분석하여 유저가 알아야 할 핵심 내용, 업데이트 사항, 이벤트 내용을 명확하고 보기 쉽게 요약해 주세요."
    ]

    async with httpx.AsyncClient() as http_client:
        for img_url in image_urls[:3]:
            try:
                resp = await http_client.get(img_url, timeout=10.0)
                if resp.status_code == 200:
                    mime_type = resp.headers.get("content-type", "image/jpeg").split(";")[0]
                    if mime_type not in ["image/jpeg", "image/png", "image/webp"]:
                        mime_type = "image/jpeg"
                    image_part = types.Part.from_bytes(data=resp.content, mime_type=mime_type)
                    contents.append(image_part)
            except Exception as e:
                logging.warning(f"이미지 다운로드 실패 ({img_url}): {e}")

    for model in ["gemini-2.5-flash", "gemini-2.0-flash-exp", "gemini-1.5-flash"]:
        try:
            response = await asyncio.to_thread(
                lambda: client.models.generate_content(
                    model=model, 
                    contents=contents
                )
            )
            if response and response.text:
                return response.text.strip()
        except Exception as e:
            logging.warning(f"모델 {model} 호출 실패: {e}")
            continue
            
    return f"공지 이미지를 요약하는 과정에서 문제가 발생했습니다. 제목: {title}"

async def check_new_notices(is_initial=False):
    global known_notices
    new_notices_found = []
    
    async with async_playwright() as p:
        browser_executable_path = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH")
        launch_kwargs = {"headless": True, "args": ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]}
        if browser_executable_path:
            launch_kwargs["executable_path"] = browser_executable_path
            
        browser = await p.chromium.launch(**launch_kwargs)
        page = await browser.new_page()
        
        try:
            await page.goto(TARGET_URL, timeout=DETAIL_TIMEOUT, wait_until="domcontentloaded")
            await page.wait_for_timeout(5000)
            
            # 공지사항 목록 링크 수집 로직
            articles = await page.query_selector_all("a")
            current_notices = []
            
            for article in articles:
                href = await article.get_attribute("href")
                text = await article.inner_text()
                if href and "/board/cm_story_view" in href:
                    if not href.startswith("http"):
                        href = "https://aion2.plaync.com" + href
                    if text.strip() and href not in [n[1] for n in current_notices]:
                        current_notices.append((text.strip(), href))
            
            if is_initial:
                for title, link in current_notices:
                    known_notices.add(link)
                logging.info(f"동기화 완료: 총 {len(known_notices)}개의 유효 공지 확인됨")
            else:
                for title, link in current_notices:
                    if link not in known_notices:
                        known_notices.add(link)
                        image_urls = await fetch_article_images(page, link)
                        summary = await generate_ai_summary_from_images(title, image_urls)
                        new_notices_found.append((title, link, summary, image_urls))
                        
        except Exception as e:
            logging.warning(f"공지 확인 중 에러 발생: {e}")
        finally:
            await browser.close()
            
    return new_notices_found

@bot.event
async def on_ready():
    logging.info(f"디스코드 봇 로그인 성공: {bot.user}")
    logging.info("부팅 시 공지사항 목록 확인 중...")
    await check_new_notices(is_initial=True)
    auto_notice_loop.start()
    
    # 💡 요청하신 빌드 완료 및 테스트 가능 안내 텍스트 로그 출력
    logging.info("빌드가 완료되었습니다. 이제 디스코드에서 테스트가 가능합니다.")

@tasks.loop(minutes=5)
async def auto_notice_loop():
    logging.info("[CHECK] 5분 주기 공지 확인 시작")
    try:
        new_notices = await check_new_notices(is_initial=False)
        for title, link, summary, image_urls in new_notices:
            for guild in bot.guilds:
                for channel in guild.text_channels:
                    if channel.name in ["공지", "update", "Aion2", "일반", "chat"]:
                        embed = discord.Embed(title="📢 아이온2 업데이트 공지", description=summary, color=discord.Color.blue())
                        embed.add_field(name="원문 링크", value=link, inline=False)
                        if image_urls:
                            embed.set_image(url=image_urls[0])
                        await channel.send(embed=embed)
                        break
    except Exception as e:
        logging.error(f"자동 감지 루프 중 에러: {e}")

@bot.command(name="확인")
async def manual_check(ctx):
    await ctx.send("🔍 최신 공지사항을 확인하고 AI가 분석 중입니다... 잠시만 기다려주세요!")
    try:
        new_notices = await check_new_notices(is_initial=False)
        if not new_notices:
            # 강제로 최신 글 하나라도 가져와서 보여주기 위한 처리
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
                page = await browser.new_page()
                await page.goto(TARGET_URL, timeout=DETAIL_TIMEOUT, wait_until="domcontentloaded")
                await page.wait_for_timeout(3000)
                articles = await page.query_selector_all("a")
                sample_link, sample_title = "", "아이온2 최신 공지"
                for art in articles:
                    h = await art.get_attribute("href")
                    t = await art.inner_text()
                    if h and "/board/cm_story_view" in h:
                        sample_link = "https://aion2.plaync.com" + h if not h.startswith("http") else h
                        sample_title = t.strip() or sample_title
                        break
                
                if sample_link:
                    image_urls = await fetch_article_images(page, sample_link)
                    summary = await generate_ai_summary_from_images(sample_title, image_urls)
                    await browser.close()
                    
                    embed = discord.Embed(title="✅ 최신 공지 이미지 분석 결과:", description=summary, color=discord.Color.green())
                    embed.add_field(name="원문 링크", value=sample_link, inline=False)
                    if image_urls:
                        embed.set_image(url=image_urls[0])
                    await ctx.send(embed=embed)
                    return
                await browser.close()
            await ctx.send("현재 새로운 감지된 공지가 없으며 샘플을 가져오지 못했습니다.")
        else:
            for title, link, summary, image_urls in new_notices:
                embed = discord.Embed(title=f"✅ {title}", description=summary, color=discord.Color.green())
                embed.add_field(name="원문 링크", value=link, inline=False)
                if image_urls:
                    embed.set_image(url=image_urls[0])
                await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(f"확인 중 오류가 발생했습니다: {e}")

@bot.command(name="테스트알림")
async def test_notification(ctx):
    await ctx.send("🔍 테스트 알림 시뮬레이션을 시작합니다...")
    try:
        sample_summary = "이것은 아이온2 업데이트 봇의 기능 테스트 메시지입니다. AI 요약 및 이미지 연동 시스템이 정상적으로 작동하고 있습니다."
        embed = discord.Embed(title="🧪 [테스트] 아이온2 공지 요약", description=sample_summary, color=discord.Color.orange())
        embed.add_field(name="테스트 링크", value=TARGET_URL, inline=False)
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(f"테스트 중 오류가 발생했습니다: {e}")

if __name__ == "__main__":
    t = Thread(target=run_flask)
    t.start()
    if DISCORD_TOKEN:
        bot.run(DISCORD_TOKEN)
    else:
        logging.error("DISCORD_TOKEN 환경 변수가 설정되지 않았습니다.")

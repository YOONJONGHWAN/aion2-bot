import os
import asyncio
import logging
import httpx
from flask import Flask
from threading import Thread
import discord
from discord.ext import commands
from playwright.async_api import async_playwright
from google import genai
from google.genai import types

# 로깅 설정
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# 환경 변수
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TARGET_CHANNEL_ID = os.getenv("DISCORD_CHANNEL_ID")
NOTICE_URL = "https://aion2.plaync.com/ko-kr/board/cm_story/list"
DETAIL_TIMEOUT = 8000
posted_notice_ids = set()

# 웹 서버 (Render 헬스체크용)
app = Flask(__name__)
@app.route('/')
def home():
    return "Aion2 Update Bot is running!", 200

def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

# 봇 설정
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

async def fetch_article_detail_and_images(page, url):
    text_content = ""
    image_urls = []
    try:
        await page.goto(url, timeout=DETAIL_TIMEOUT, wait_until="domcontentloaded")
        await page.wait_for_timeout(1500)
        body = await page.query_selector("body")
        if body:
            text_content = (await body.inner_text()).strip()
        
        img_elements = await page.query_selector_all("article img, .board_view img, div[class*='content'] img, body img")
        for img in img_elements:
            src = await img.get_attribute("src")
            if src:
                if src.startswith("//"):
                    src = "https:" + src
                elif src.startswith("/"):
                    src = "https://aion2.plaync.com" + src
                if "http" in src and not any(x in src.lower() for x in ["logo", "icon", "avatar"]):
                    image_urls.append(src)
    except Exception as e:
        logging.warning(f"상세 수집 실패: {e}")
    return text_content, image_urls

async def generate_ai_summary(title, content, image_urls):
    if not GEMINI_API_KEY:
        return None
    client = genai.Client(api_key=GEMINI_API_KEY)
    
    prompt = f"게임 '아이온2' 공지사항입니다. 제목과 본문을 분석하여 사용자가 핵심 내용을 빠르게 이해할 수 있도록 명확하게 정리해 주세요.\n\n제목: {title}\n"
    contents = [prompt]

    if len(content) >= 30:
        contents.append(f"본문:\n{content[:2500]}")
    elif image_urls:
        try:
            async with httpx.AsyncClient() as http_client:
                resp = await http_client.get(image_urls[0], timeout=10.0)
                if resp.status_code == 200:
                    image_part = types.Part.from_bytes(data=resp.content, mime_type=resp.headers.get("content-type", "image/jpeg").split(";")[0])
                    contents.append(image_part)
                    contents.append("이 이미지는 공지사항의 본문입니다. 내용을 분석하여 핵심 내용을 요약해 주세요.")
        except:
            pass

    for model in ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]:
        try:
            response = await asyncio.to_thread(lambda: client.models.generate_content(model=model, contents=contents))
            if response and response.text:
                return response.text.strip()
        except:
            continue
    return None

async def init_posted_notices():
    global posted_notice_ids
    logging.info("부팅 시 공지사항 목록 확인 중...")
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=['--no-sandbox'])
            page = await browser.new_page()
            
            await page.goto(NOTICE_URL, timeout=30000, wait_until="domcontentloaded")
            
            # [핵심 수정] 게시판 목록이 화면에 렌더링될 때까지 최대 10초 대기
            try:
                await page.wait_for_selector("a", timeout=10000)
            except:
                pass
            await page.wait_for_timeout(3000) # 추가 안정화 대기
            
            elements = await page.query_selector_all("a")
            logging.info(f"발견된 전체 링크 개수: {len(elements)}")
            
            for elem in elements:
                href = await elem.get_attribute("href")
                if href and 'cm_story' in href and ('articleId=' in href or 'detail' in href or 'view' in href):
                    full_url = href if href.startswith("http") else f"https://aion2.plaync.com{href}"
                    clean_url = full_url.split("?")[0] + ("?" + full_url.split("?")[1] if "?" in full_url else "")
                    posted_notice_ids.add(clean_url)
                        
            await browser.close()
            logging.info(f"동기화 완료: 총 {len(posted_notice_ids)}개의 유효 공지 확인됨")
    except Exception as e:
        logging.warning(f"공지 확인 중 에러 발생: {e}")

async def scrape_and_process_notices(is_test=False):
    new_notices = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox'])
        context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
        page = await context.new_page()
        try:
            await page.goto(NOTICE_URL, timeout=30000, wait_until="domcontentloaded")
            
            # [핵심 수정] 렌더링 완료 대기
            try:
                await page.wait_for_selector("a", timeout=10000)
            except:
                pass
            await page.wait_for_timeout(3000)
            
            elements = await page.query_selector_all("a")
            targets = []
            seen_urls = set()
            
            for elem in elements:
                href = await elem.get_attribute("href")
                title = (await elem.inner_text()).strip()
                
                if href and 'cm_story' in href and ('articleId=' in href or 'detail' in href or 'view' in href):
                    if not title or len(title) < 5:
                        continue
                    
                    full_url = href if href.startswith("http") else f"https://aion2.plaync.com{href}"
                    notice_id = full_url.split("?")[0] + ("?" + full_url.split("?")[1] if "?" in full_url else "")
                    
                    if notice_id in seen_urls: continue
                    seen_urls.add(notice_id)
                    
                    if notice_id in posted_notice_ids and not is_test: continue
                    
                    targets.append({"id": notice_id, "title": title, "url": full_url})
                    if is_test and len(targets) >= 1: 
                        break

            for target in targets:
                try:
                    detail_page = await context.new_page()
                    content, img_urls = await fetch_article_detail_and_images(detail_page, target['url'])
                    await detail_page.close()
                    
                    summary = await generate_ai_summary(target['title'], content, img_urls)
                    
                    new_notices.append({
                        "title": target['title'], 
                        "url": target['url'], 
                        "summary": summary, 
                        "image": img_urls[0] if img_urls else None
                    })
                    
                    posted_notice_ids.add(target['id'])
                except Exception as e:
                    logging.warning(f"개별 공지 처리 중 에러 ({target['title']}): {e}")

        finally:
            await browser.close()
            
    return new_notices

def create_notice_embed(notice):
    embed = discord.Embed(
        title=f"📢 {notice['title']}",
        url=notice['url'],
        color=discord.Color.blue()
    )
    if notice['summary']:
        embed.add_field(name="📝 AI 핵심 요약", value=notice['summary'], inline=False)
    else:
        embed.add_field(name="📝 안내", value="상세 내용은 링크를 확인해 주세요.", inline=False)
        
    if notice['image']:
        embed.set_image(url=notice['image'])
        
    embed.set_footer(text="Aion2 Update Bot • 자동 감지 시스템")
    return embed

@bot.event
async def on_ready():
    logging.info(f"디스코드 봇 로그인 성공: {bot.user}")
    await init_posted_notices()
    bot.loop.create_task(auto_check_loop())

async def auto_check_loop():
    await bot.wait_until_ready()
    logging.info("5분 주기 자동 공지 감지 시스템이 시작되었습니다.")
    while not bot.is_closed():
        try:
            logging.info("[CHECK] 5분 주기 공지 확인 시작")
            notices = await scrape_and_process_notices(is_test=False)
            if notices and TARGET_CHANNEL_ID:
                channel = bot.get_channel(int(TARGET_CHANNEL_ID))
                if channel:
                    for n in notices:
                        embed = create_notice_embed(n)
                        await channel.send(embed=embed)
                        logging.info(f"[전송 완료] {n['title']}")
        except Exception as e:
            logging.error(f"자동 감지 루프 중 에러: {e}")
        await asyncio.sleep(300)

@bot.command(name="확인", aliases=["테스트알림"])
async def test_notification(ctx):
    await ctx.send("🔍 최신 공지사항을 확인하고 요약을 진행 중입니다...")
    try:
        notices = await scrape_and_process_notices(is_test=True)
        if notices:
            embed = create_notice_embed(notices[0])
            await ctx.send(content="✅ **최신 공지 테스트 결과:**", embed=embed)
        else:
            await ctx.send("❌ 공지사항을 가져오지 못했거나 새 공지가 없습니다.")
    except Exception as e:
        await ctx.send(f"⚠️ 오류 발생: {e}")

if __name__ == "__main__":
    Thread(target=run_flask, daemon=True).start()
    bot.run(DISCORD_TOKEN)

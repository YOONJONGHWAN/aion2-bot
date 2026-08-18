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

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TARGET_CHANNEL_ID = os.getenv("DISCORD_CHANNEL_ID")
NOTICE_URL = "https://aion2.plaync.com/ko-kr/board/cm_story/list"
DETAIL_TIMEOUT = 15000
posted_notice_ids = set()

app = Flask(__name__)
@app.route('/')
def home():
    return "Aion2 Update Bot is running!", 200

def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

async def fetch_article_images(page, url):
    image_urls = []
    try:
        await page.goto(url, timeout=DETAIL_TIMEOUT, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)
        
        img_elements = await page.query_selector_all("article img, .board_view img, div[class*='content'] img, body img")
        for img in img_elements:
            src = await img.get_attribute("src")
            if src:
                if src.startswith("//"):
                    src = "https:" + src
                elif src.startswith("/"):
                    src = "https://aion2.plaync.com" + src
                
                if "http" in src and not any(x in src.lower() for x in ["logo", "icon", "avatar", "banner", "profile"]):
                    if src not in image_urls:
                        image_urls.append(src)
    except Exception as e:
        logging.warning(f"이미지 수집 실패: {e}")
    return image_urls

async def generate_ai_summary_from_images(title, image_urls):
    if not GEMINI_API_KEY or not image_urls:
        return f"새로운 공지사항이 등록되었습니다. (이미지 형태의 공지)"
    
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

    for model in ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]:
        try:
            response = await asyncio.to_thread(lambda: client.models.generate_content(model=model, contents=contents))
            if response and response.text:
                return response.text.strip()
        except:
            continue
            
    return "공지 이미지를 요약하는 과정에서 문제가 발생했습니다. 링크를 확인해 주세요."

async def get_notice_targets(page):
    targets = []
    try:
        elements = await page.query_selector_all("a")
        seen_urls = set()
        
        for elem in elements:
            href = await elem.get_attribute("href")
            title = (await elem.inner_text()).strip()
            
            if not href:
                continue
                
            if 'articleId=' in href:
                full_url = href if href.startswith("http") else f"https://aion2.plaync.com{href}"
                
                if full_url in seen_urls:
                    continue
                seen_urls.add(full_url)
                
                if not title or len(title) < 2:
                    title = "아이온2 업데이트 공지"
                    
                targets.append({"id": full_url, "title": title, "url": full_url})
    except Exception as e:
        logging.error(f"공지 타겟 추출 중 오류: {e}")
        
    return targets

async def init_posted_notices():
    global posted_notice_ids
    logging.info("부팅 시 공지사항 목록 확인 중...")
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True, 
                args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage', '--disable-gpu']
            )
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 1080}
            )
            page = await context.new_page()
            
            await page.goto(NOTICE_URL, timeout=30000, wait_until="domcontentloaded")
            try:
                await page.wait_for_selector("a", timeout=15000)
            except:
                pass
            await page.wait_for_timeout(4000)
            
            targets = await get_notice_targets(page)
            for t in targets:
                posted_notice_ids.add(t['id'])
                        
            await browser.close()
            logging.info(f"동기화 완료: 총 {len(posted_notice_ids)}개의 유효 공지 확인됨")
    except Exception as e:
        logging.warning(f"공지 확인 중 에러 발생: {e}")

async def scrape_and_process_notices(is_test=False):
    new_notices = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True, 
            args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage', '--disable-gpu']
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080}
        )
        page = await context.new_page()
        try:
            await page.goto(NOTICE_URL, timeout=30000, wait_until="domcontentloaded")
            try:
                await page.wait_for_selector("a", timeout=15000)
            except:
                pass
            await page.wait_for_timeout(4000)
            
            targets = await get_notice_targets(page)
            valid_targets = []
            
            for target in targets:
                if target['id'] in posted_notice_ids and not is_test:
                    continue
                valid_targets.append(target)
                if is_test:
                    break

            for target in valid_targets:
                try:
                    detail_page = await context.new_page()
                    img_urls = await fetch_article_images(detail_page, target['url'])
                    await detail_page.close()
                    
                    summary = await generate_ai_summary_from_images(target['title'], img_urls)
                    
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
        embed.add_field(name="📝 이미지 공지 AI 핵심 요약", value=notice['summary'], inline=False)
    else:
        embed.add_field(name="📝 안내", value="상세 내용은 링크를 확인해 주세요.", inline=False)
        
    if notice['image']:
        embed.set_image(url=notice['image'])
        
    embed.set_footer(text="Aion2 Update Bot • 이미지 자동 분석 시스템")
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
    await ctx.send("🔍 이미지 공지사항을 다운로드하여 AI가 분석 중입니다... 잠시만 기다려주세요!")
    try:
        notices = await scrape_and_process_notices(is_test=True)
        if notices:
            embed = create_notice_embed(notices[0])
            await ctx.send(content="✅ **최신 공지 이미지 분석 결과:**", embed=embed)
        else:
            await ctx.send("❌ 공지사항을 가져오지 못했거나 새 공지가 없습니다.")
    except Exception as e:
        await ctx.send(f"⚠️ 오류 발생: {e}")

if __name__ == "__main__":
    Thread(target=run_flask, daemon=True).start()
    bot.run(DISCORD_TOKEN)

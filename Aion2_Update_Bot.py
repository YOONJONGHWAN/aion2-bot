import os
import asyncio
from threading import Thread
from flask import Flask
import discord
from discord.ext import commands
from playwright.async_api import async_playwright

# 1. Flask 서버 설정 (Render 바인딩 및 Keep-Alive용)
app = Flask('')

@app.route('/')
def home():
    return "Bot is running!"

def run_flask():
    # Render에서 지정해주는 PORT 환경변수 연결 (기본값 8080)
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()

# 2. 디스코드 봇 설정
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# ⚠️ 크롤링할 실제 CM 스토리 게시판 URL 주소로 변경하세요.
TARGET_URL = "https://aion2.plaync.com/" 

# 3. Playwright 크롤링 함수 (Render 환경 최적화)
async def fetch_latest_post():
    async with async_playwright() as p:
        # 리눅스 컨테이너 환경 필수 옵션 지정
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",  # Shared Memory 부족으로 인한 크래시 방지
                "--disable-gpu",
                "--no-first-run",
                "--no-zygote",
                "--single-process"
            ]
        )
        
        # 실제 일반 브라우저처럼 보이도록 User-Agent 및 뷰포트 설정
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/123.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 720}
        )
        
        page = await context.new_page()
        
        try:
            # 타임아웃 60초 설정 및 HTML 구조만 먼저 로드되면 진행
            await page.goto(TARGET_URL, timeout=60000, wait_until="domcontentloaded")
            
            # 동적 스크립트 실행을 위해 3초 대기
            await asyncio.sleep(3)
            
            # TODO: 크롤링하려는 페이지의 실제 게시글 제목/링크 CSS 셀렉터로 수정 필요
            # 예시 selector: '.board-list .title' 또는 'a.post-link'
            title_element = await page.query_selector('a') 
            
            if title_element:
                title = await title_element.inner_text()
                link = await title_element.get_attribute('href')
                
                # 상대 경로 링크 처리 (예: /board/123 -> https://domain.com/board/123)
                if link and link.startswith('/'):
                    base_url = "/".join(TARGET_URL.split("/")[:3])
                    link = base_url + link
                    
                return {"title": title.strip(), "link": link}
            else:
                print("[WARN] 지정한 게시글 요소를 찾지 못했습니다.")
                return None

        except Exception as e:
            print(f"[ERROR] Playwright 크롤링 에러 발생: {e}")
            return None
        finally:
            await browser.close()

# 4. 디스코드 봇 이벤트 및 명령어
@bot.event
async def on_ready():
    print(f"[INFO] 디스코드 봇 로그인 완료: {bot.user.name}")

@bot.command(name='확인')
async def check_update(ctx):
    await ctx.send("CM 스토리 최신 게시글을 확인하는 중입니다...")
    
    post = await fetch_latest_post()
    
    if post:
        title = post.get("title", "제목 없음")
        link = post.get("link", TARGET_URL)
        await ctx.send(f"📢 **최신 게시글:** {title}\n🔗 {link}")
    else:
        await ctx.send("게시글을 불러오지 못했습니다. (사이트 차단 또는 셀렉터 확인 필요)")

# 5. 메인 실행
if __name__ == "__main__":
    # 웹 서버 실행 (Render 포트 바인딩)
    keep_alive()
    
    # Render의 Environment Variables에 설정된 DISCORD_TOKEN을 읽어옴
    TOKEN = os.environ.get("DISCORD_TOKEN")
    
    if TOKEN:
        bot.run(TOKEN)
    else:
        print("[CRITICAL] DISCORD_TOKEN 환경변수가 설정되지 않았습니다.")

import os
import asyncio
import subprocess
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

# AION2 CM 스토리 게시판 목록 페이지 URL
TARGET_URL = "https://aion2.plaync.com/ko-kr/board/cm_story/list"

# 3. Playwright 크롤링 함수 (브라우저 유실 시 자동 재설치 로직 포함)
async def fetch_latest_post():
    async with async_playwright() as p:
        browser_args = [
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--no-first-run",
            "--no-zygote",
            "--single-process"
        ]
        
        try:
            browser = await p.chromium.launch(headless=True, args=browser_args)
        except Exception as launch_err:
            # 브라우저 실행 파일이 없는 에러 발생 시 즉시 감지 후 자동 설치
            if "Executable doesn't exist" in str(launch_err):
                print("[INFO] 크롬 브라우저 실행 파일이 없어 자동 설치를 진행합니다...")
                subprocess.run(["playwright", "install", "chromium"])
                browser = await p.chromium.launch(headless=True, args=browser_args)
            else:
                raise launch_err
        
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
            # 페이지 이동 (최대 60초 대기)
            await page.goto(TARGET_URL, timeout=60000, wait_until="domcontentloaded")
            
            # 동적 게시판 로딩 대기 (class="title"인 a 태그)
            await page.wait_for_selector('a.title', timeout=15000)
            
            # 가장 첫 번째 게시글(최신글) 요소 가져오기
            title_element = await page.query_selector('a.title')
            
            if title_element:
                title = await title_element.inner_text()
                link = await title_element.get_attribute('href')
                
                if link and link.startswith('/'):
                    link = "https://aion2.plaync.com" + link
                    
                return {"title": title.strip(), "link": link}
            else:
                print("[WARN] 'a.title' 요소를 찾지 못했습니다.")
                return None

        except Exception as e:
            print(f"[ERROR] 크롤링 실패: {e}")
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
        await ctx.send("게시글을 불러오지 못했습니다. 사이트 로딩 시간이 초과되었거나 구조가 변경되었을 수 있습니다.")

# 5. 메인 실행
if __name__ == "__main__":
    keep_alive()
    
    TOKEN = os.environ.get("DISCORD_TOKEN")
    
    if TOKEN:
        bot.run(TOKEN)
    else:
        print("[CRITICAL] DISCORD_TOKEN 환경변수가 설정되지 않았습니다.")

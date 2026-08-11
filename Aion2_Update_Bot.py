import os
import asyncio
import subprocess
from threading import Thread
from flask import Flask
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

import discord
from discord.ext import commands, tasks

# ---------------- [무료 서버 유지용 Flask 웹서버] ----------------
app = Flask('')

@app.route('/')
def home():
    return "Aion2 Bot is Running 24/7 (Playwright Mode)!"

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()

# ---------------- [디스코드 봇 및 환경 설정] ----------------
TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = 1536734023982911639  # 본인 디스코드 채널 ID

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

seen_article_ids = set()

# ---------------- [봇 이벤트] ----------------
@bot.event
async def on_ready():
    print(f"로그인 성공: {bot.user.name}")
    print("아이온2 공식 홈페이지 헤드리스 브라우저 감지 봇 가동 시작! (5분 주기)")
    if not check_aion2_updates.is_running():
        check_aion2_updates.start()

# ---------------- [봇 명령어] ----------------
@bot.command()
async def 안녕(ctx):
    await ctx.send("안녕하세요! 아이온2 공식 공지 브라우저 연동 봇입니다.")

@bot.command()
async def 확인(ctx):
    print("[디버그] '!확인' 명령어 수신됨 (브라우저 가동 중)...")
    await ctx.send("아이온2 공식 홈페이지에 접속하여 최신 공지를 확인하는 중입니다... (약 3~5초 소요)")

    articles = await get_latest_official_notices_with_browser()
    if articles:
        for latest in articles[:3]:
            embed = discord.Embed(
                title=f"📢 {latest['title']}",
                url=latest['link'],
                description="클릭하면 아이온2 공식 홈페이지 공지 원문으로 이동합니다.",
                color=0x00aeef
            )
            embed.set_footer(text="Aion2 Official Browser Bot")
            await ctx.send(embed=embed)
    else:
        await ctx.send("공지사항을 가져오지 못했습니다. Render 로그를 확인해 주세요.")

# ---------------- [헤드리스 브라우저 공지 크롤링 함수] ----------------
async def get_latest_official_notices_with_browser():
    articles = []
    target_url = "https://aion2.plaync.com/ko-kr/board/notice/list"

    async with async_playwright() as p:
        try:
            # 1차 브라우저 실행 시도
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-accelerated-2d-canvas",
                    "--disable-gpu"
                ]
            )
        except Exception as e:
            print(f"[디버그] 브라우저 실행 실패, 자동 설치 시도 중... 오류: {e}")
            # 실행 파일이 없거나 경로가 어긋났을 때 코드가 직접 브라우저 설치를 시도
            subprocess.run(["playwright", "install", "chromium"], capture_output=True)
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-accelerated-2d-canvas",
                    "--disable-gpu"
                ]
            )

        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800}
        )
        page = await context.new_page()

        try:
            print("[디버그] 브라우저로 공식 홈페이지 접속 중...")
            await page.goto(target_url, timeout=30000, wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)

            html_text = await page.content()
            soup = BeautifulSoup(html_text, "html.parser")

            post_items = soup.select("a[href*='/board/notice/view']")
            if not post_items:
                post_items = soup.select("a.board_item, a.item_link, a")

            for item in post_items:
                title = item.get("title") or item.get_text(strip=True)
                link = item.get("href")

                if not title or not link:
                    continue

                if link.startswith("/"):
                    link = "https://aion2.plaync.com" + link

                if "/board/notice/view" in link:
                    if "articleId=" in link:
                        article_id = link.split("articleId=")[1].split("&")[0]
                    else:
                        article_id = link

                    if not any(a["id"] == article_id for a in articles):
                        if len(title) > 2:
                            articles.append({
                                "id": article_id,
                                "title": title,
                                "link": link
                            })

            print(f"[디버그] 브라우저 수집 성공 공지 수: {len(articles)}")

        except Exception as e:
            print(f"[디버그] 브라우저 크롤링 중 예외 발생: {e}")
        finally:
            await browser.close()

    return articles

# ---------------- [5분 주기 자동 체크 루프] ----------------
@tasks.loop(minutes=5)
async def check_aion2_updates():
    global seen_article_ids

    channel = bot.get_channel(CHANNEL_ID)
    if not channel:
        return

    articles = await get_latest_official_notices_with_browser()
    if not articles:
        return

    if not seen_article_ids:
        for item in articles:
            seen_article_ids.add(item["id"])
        print(f"기준 공식 공지 목록 설정 완료: {len(seen_article_ids)}개")
        return

    new_articles = [item for item in articles if item["id"] not in seen_article_ids]

    for item in reversed(new_articles):
        seen_article_ids.add(item["id"])

        embed = discord.Embed(
            title=f"📢 [아이온2 공식 공지] {item['title']}",
            url=item["link"],
            description="아이온2 공식 홈페이지에 새로운 공지가 등록되었습니다!",
            color=0x00a8ff
        )
        embed.set_footer(text="Aion2 Official Browser Bot")

        await channel.send(embed=embed)
        print(f"새 공식 공지 알림 전송 완료: {item['title']}")

# ---------------- [실행] ----------------
keep_alive()
bot.run(TOKEN)

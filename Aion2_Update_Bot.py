import os
import asyncio
import aiohttp
from bs4 import BeautifulSoup
from threading import Thread
from flask import Flask

import discord
from discord.ext import commands, tasks

# ---------------- [무료 서버 유지용 Flask 웹서버] ----------------
app = Flask('')

@app.route('/')
def home():
    return "Aion2 Bot is Running 24/7!"

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
    print("아이온2 알림 봇이 24시간 감지를 시작합니다. (5분 주기)")
    if not check_aion2_updates.is_running():
        check_aion2_updates.start()

# ---------------- [봇 명령어] ----------------
@bot.command()
async def 안녕(ctx):
    await ctx.send("안녕하세요! 아이온2 업데이트 알림 봇입니다.")

@bot.command()
async def 확인(ctx):
    print("[디버그] '!확인' 명령어 수신됨!")
    await ctx.send("아이온2 게시판에서 최근 글 목록을 확인하는 중입니다...")

    articles = await get_latest_articles()
    if articles:
        for latest in articles[:3]:
            embed = discord.Embed(
                title=f"📢 {latest['title']}",
                url=latest['link'],
                description="클릭하면 아이온2 공식 홈페이지 게시글로 이동합니다.",
                color=0x3498db
            )
            if latest['image']:
                embed.set_image(url=latest['image'])
            embed.set_footer(text="Aion2 Notification Bot • 공식 CM 스토리")
            await ctx.send(embed=embed)
    else:
        await ctx.send("게시글을 가져오는 데 실패했거나 글이 없습니다. (Render 로그를 확인해 주세요)")

# ---------------- [크롤링 함수] ----------------
async def get_latest_articles():
    articles = []
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://aion2.plaync.com/"
    }

    timeout = aiohttp.ClientTimeout(total=7)

    async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
        web_url = "https://aion2.plaync.com/ko-kr/board/cm_story/list"
        try:
            print("[디버그] 공식 홈페이지 HTML 요청 시작...")
            async with session.get(web_url) as resp:
                print(f"[디버그] 응답 상태 코드: {resp.status}")
                if resp.status == 200:
                    html_text = await resp.text()
                    soup = BeautifulSoup(html_text, "html.parser")
                    
                    # 공홈 게시판 링크 패턴 탐색
                    links = soup.find_all("a")
                    for a in links:
                        href = a.get("href", "")
                        if "articleId=" in href:
                            article_id = href.split("articleId=")[-1].split("&")[0]
                            title = a.get_text(strip=True)
                            
                            # 제목이 너무 짧거나 비어있으면 건너뜀
                            if not title or len(title) < 2:
                                continue
                                
                            full_link = href if href.startswith("http") else f"https://aion2.plaync.com{href}"
                            
                            # 중복 방지 및 리스트 추가
                            if not any(item["id"] == article_id for item in articles):
                                articles.append({
                                    "id": article_id, 
                                    "title": title, 
                                    "link": full_link, 
                                    "image": None
                                })
                    
                    print(f"[디버그] 수집된 게시글 수: {len(articles)}")
                    if articles:
                        return articles
                else:
                    print(f"[디버그] 페이지 접근 실패: {resp.status}")
        except Exception as e:
            print(f"[디버그] 크롤링 예외 발생: {e}")

    return articles

# ---------------- [5분 주기 자동 체크 루프] ----------------
@tasks.loop(minutes=5)
async def check_aion2_updates():
    global seen_article_ids
    
    channel = bot.get_channel(CHANNEL_ID)
    if not channel:
        return

    articles = await get_latest_articles()
    if not articles:
        return

    if not seen_article_ids:
        for item in articles:
            seen_article_ids.add(item["id"])
        print(f"기준 게시글 목록 설정 완료: {seen_article_ids}")
        return

    new_articles = [item for item in articles if item["id"] not in seen_article_ids]
    
    for item in reversed(new_articles):
        seen_article_ids.add(item["id"])
        
        embed = discord.Embed(
            title=f"📢 [아이온2 새 소식] {item['title']}",
            url=item["link"],
            description="아이온2 공식 홈페이지에 새로운 게시글이 등록되었습니다!",
            color=0x00a8ff
        )
        embed.set_footer(text="Aion2 Notification Bot")
        
        await channel.send(embed=embed)
        print(f"새 업데이트 알림 전송 완료: {item['title']}")

# ---------------- [실행] ----------------
keep_alive()
bot.run(TOKEN)

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
    print("아이온2 공식 홈페이지 전용 봇이 24시간 감지를 시작합니다. (5분 주기)")
    if not check_aion2_updates.is_running():
        check_aion2_updates.start()

# ---------------- [봇 명령어] ----------------
@bot.command()
async def 안녕(ctx):
    await ctx.send("안녕하세요! 아이온2 공식 홈페이지 공지 알림 봇입니다.")

@bot.command()
async def 확인(ctx):
    print("[디버그] '!확인' 명령어 수신됨!")
    await ctx.send("아이온2 공식 홈페이지 공지사항을 확인하는 중입니다...")

    articles = await get_latest_official_notices()
    if articles:
        for latest in articles[:3]:
            embed = discord.Embed(
                title=f"📢 {latest['title']}",
                url=latest['link'],
                description="클릭하면 아이온2 공식 홈페이지 공지 원문으로 이동합니다.",
                color=0x00aeef
            )
            embed.set_footer(text="Aion2 Official Notification Bot")
            await ctx.send(embed=embed)
    else:
        await ctx.send("공식 홈페이지에서 공지사항을 가져오지 못했습니다. (Render 로그를 확인해 주세요)")

# ---------------- [공식 홈페이지 직접 크롤링 함수] ----------------
async def get_latest_official_notices():
    articles = []
    
    # 아이온2 공식 홈페이지 공지사항 URL
    target_url = "https://aion2.plaync.com/ko-kr/board/notice/list"
    
    # 엔씨 보안 방화벽 우회를 위한 고성능 브라우저 헤더 시뮬레이션
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://aion2.plaync.com/",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache"
    }

    timeout = aiohttp.ClientTimeout(total=10)

    async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
        try:
            print("[디버그] 공식 홈페이지 직접 접속 요청...")
            async with session.get(target_url) as resp:
                print(f"[디버그] 응답 상태 코드: {resp.status}")
                if resp.status == 200:
                    html_text = await resp.text()
                    soup = BeautifulSoup(html_text, "html.parser")
                    
                    # 공식 홈페이지 게시판 내의 공지사항 링크 및 타이틀 요소 탐색
                    # (플레이엔씨 공통 게시판 구조 셀렉터 적용)
                    post_items = soup.select("a.board_item, a.item_link, .board_list_wrap a, a[href*='/board/notice/view']")
                    
                    if not post_items:
                        # 구조가 동적일 경우 일반적인 본문 내 모든 공지형 링크 스캔
                        post_items = soup.find_all("a", href=lambda href: href and "/board/notice/view" in href)

                    for item in post_items:
                        title = item.get("title") or item.get_text(strip=True)
                        link = item.get("href")
                        
                        if not title or not link:
                            continue
                            
                        # 상대 경로일 경우 절대 경로로 변환
                        if link.startswith("/"):
                            link = "https://aion2.plaync.com" + link
                            
                        # 고유 식별자 추출 (articleId 파라미터)
                        if "articleId=" in link:
                            article_id = link.split("articleId=")[1].split("&")[0]
                        else:
                            article_id = link
                            
                        if not any(a["id"] == article_id for a in articles):
                            # 빈 타이틀이나 메뉴 바라이트 텍스트 제외
                            if len(title) > 2:
                                articles.append({
                                    "id": article_id,
                                    "title": title,
                                    "link": link
                                })
                    
                    print(f"[디버그] 수집된 공식 공지 수: {len(articles)}")
                    return articles
                else:
                    print(f"[디버그] 공식 홈페이지 접속 차단 또는 실패: {resp.status}")
        except Exception as e:
            print(f"[디버그] 공식 홈페이지 크롤링 예외 발생: {e}")

    return articles

# ---------------- [5분 주기 자동 체크 루프] ----------------
@tasks.loop(minutes=5)
async def check_aion2_updates():
    global seen_article_ids
    
    channel = bot.get_channel(CHANNEL_ID)
    if not channel:
        return

    articles = await get_latest_official_notices()
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
        embed.set_footer(text="Aion2 Official Notification Bot")
        
        await channel.send(embed=embed)
        print(f"새 공식 공지 알림 전송 완료: {item['title']}")

# ---------------- [실행] ----------------
keep_alive()
bot.run(TOKEN)

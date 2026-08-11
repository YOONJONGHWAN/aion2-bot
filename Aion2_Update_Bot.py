import os
import asyncio
import aiohttp
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
    print("아이온2 공식 공지 알림 봇이 24시간 감지를 시작합니다. (5분 주기)")
    if not check_aion2_updates.is_running():
        check_aion2_updates.start()

# ---------------- [봇 명령어] ----------------
@bot.command()
async def 안녕(ctx):
    await ctx.send("안녕하세요! 아이온2 공식 공지 알림 봇입니다.")

@bot.command()
async def 확인(ctx):
    print("[디버그] '!확인' 명령어 수신됨!")
    await ctx.send("아이온2 공식 홈페이지에서 최신 공지사항을 확인하는 중입니다...")

    articles = await get_latest_articles()
    if articles:
        for latest in articles[:3]:
            embed = discord.Embed(
                title=f"📢 {latest['title']}",
                url=latest['link'],
                description="클릭하면 아이온2 공식 공지사항으로 이동합니다.",
                color=0x00aeef
            )
            embed.set_footer(text="Aion2 Notification Bot • 공식 홈페이지 공지")
            await ctx.send(embed=embed)
    else:
        await ctx.send("공지사항을 가져오는 데 실패했거나 데이터가 없습니다. (Render 로그를 확인해 주세요)")

# ---------------- [플레이엔씨 공식 공지 API 크롤링 함수] ----------------
async def get_latest_articles():
    articles = []
    
    # 플레이엔씨 아이온2 공식 공지사항 API 엔드포인트
    api_url = "https://aion2.plaync.com/api/v1/board/notice/list?page=1&size=10"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://aion2.plaync.com/ko-kr/board/notice/list"
    }

    timeout = aiohttp.ClientTimeout(total=7)

    async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
        try:
            print("[디버그] 공식 공지 API 요청 시작...")
            async with session.get(api_url) as resp:
                print(f"[디버그] 응답 상태 코드: {resp.status}")
                if resp.status == 200:
                    data = await resp.json()
                    
                    # 공식 API 데이터 구조에서 게시글 리스트 추출
                    item_list = data.get("result", {}).get("list", []) or data.get("list", [])
                    
                    for item in item_list:
                        article_id = str(item.get("articleId") or item.get("id", ""))
                        title = item.get("title", "")
                        
                        if not article_id or not title:
                            continue
                            
                        link = f"https://aion2.plaync.com/ko-kr/board/notice/view?articleId={article_id}"
                        
                        articles.append({
                            "id": article_id,
                            "title": title,
                            "link": link
                        })
                    
                    print(f"[디버그] 수집된 공식 공지 수: {len(articles)}")
                    return articles
                else:
                    print(f"[디버그] API 응답 실패: {resp.status}")
        except Exception as e:
            print(f"[디버그] API 예외 발생: {e}")

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
        print(f"기준 공지 목록 설정 완료: {len(seen_article_ids)}개")
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
        embed.set_footer(text="Aion2 Notification Bot")
        
        await channel.send(embed=embed)
        print(f"새 공식 공지 알림 전송 완료: {item['title']}")

# ---------------- [실행] ----------------
keep_alive()
bot.run(TOKEN)

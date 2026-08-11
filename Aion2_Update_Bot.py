import os
import asyncio
import aiohttp
import xml.etree.ElementTree as ET
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
    await ctx.send("구글 뉴스 RSS에서 아이온2 소식을 확인하는 중입니다...")

    articles = await get_latest_articles()
    if articles:
        for latest in articles[:3]:
            embed = discord.Embed(
                title=f"📢 {latest['title']}",
                url=latest['link'],
                description="클릭하면 관련 소식 페이지로 이동합니다.",
                color=0x3498db
            )
            embed.set_footer(text="Aion2 Notification Bot • 구글 뉴스 RSS 연동")
            await ctx.send(embed=embed)
    else:
        await ctx.send("가져온 소식이 없습니다. 잠시 후 다시 시도해 주세요.")

# ---------------- [구글 뉴스 RSS 크롤링 함수 (차단 없음)] ----------------
async def get_latest_articles():
    articles = []
    
    # 구글 뉴스 RSS (아이온2 검색 결과 고속 피드)
    rss_url = "https://news.google.com/rss/search?q=%EC%95%84%EC%9D%B4%EC%98%A82&hl=ko&gl=KR&ceid=KR:ko"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    }

    timeout = aiohttp.ClientTimeout(total=7)

    async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
        try:
            print("[디버그] 구글 뉴스 RSS 요청 시작...")
            async with session.get(rss_url) as resp:
                print(f"[디버그] 응답 상태 코드: {resp.status}")
                if resp.status == 200:
                    xml_data = await resp.text()
                    
                    # XML 파싱
                    root = ET.fromstring(xml_data)
                    items = root.findall(".//item")
                    
                    for item in items:
                        title = item.find("title").text if item.find("title") is not None else ""
                        link = item.find("link").text if item.find("link") is not None else ""
                        guid = item.find("guid").text if item.find("guid") is not None else link
                        
                        if not title or not link:
                            continue
                            
                        if not any(a["id"] == guid for a in articles):
                            articles.append({
                                "id": guid,
                                "title": title,
                                "link": link
                            })
                    
                    print(f"[디버그] 수집된 RSS 소식 수: {len(articles)}")
                    return articles
                else:
                    print(f"[디버그] RSS 응답 실패: {resp.status}")
        except Exception as e:
            print(f"[디버그] RSS 예외 발생: {e}")

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
        print(f"기준 게시글 목록 설정 완료: {len(seen_article_ids)}개")
        return

    new_articles = [item for item in articles if item["id"] not in seen_article_ids]
    
    for item in reversed(new_articles):
        seen_article_ids.add(item["id"])
        
        embed = discord.Embed(
            title=f"📢 [아이온2 새 소식] {item['title']}",
            url=item["link"],
            description="새로운 아이온2 관련 소식이 등록되었습니다!",
            color=0x00a8ff
        )
        embed.set_footer(text="Aion2 Notification Bot")
        
        await channel.send(embed=embed)
        print(f"새 업데이트 알림 전송 완료: {item['title']}")

# ---------------- [실행] ----------------
keep_alive()
bot.run(TOKEN)

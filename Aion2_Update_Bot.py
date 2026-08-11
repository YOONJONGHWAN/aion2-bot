import os
import asyncio
import requests
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
CHANNEL_ID = 1536734023982911639

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
    await ctx.send("아이온2 게시판에서 최근 글 목록을 확인하는 중입니다...")

    articles = await asyncio.to_thread(get_latest_articles)
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

# ---------------- [개선된 크롤링 함수 (보안 헤더 강화)] ----------------
def get_latest_articles():
    articles = []
    
    # 실제 크롬 브라우저와 동일한 브라우저 헤더 세팅 (우회용)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://aion2.plaync.com/ko-kr/board/cm_story/list",
        "Origin": "https://aion2.plaync.com",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Sec-Ch-Ua": '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin"
    }

    # 1. API 호출 방식
    api_url = "https://aion2.plaync.com/api/board/cm_story/list?page=1&size=5"
    try:
        response = requests.get(api_url, headers=headers, timeout=10)
        print(f"[디버그] API 응답 코드: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            posts = []
            if isinstance(data, dict):
                posts = data.get("list") or data.get("data") or data.get("contents") or []
            elif isinstance(data, list):
                posts = data

            for post in posts:
                article_id = str(post.get("articleId") or post.get("id") or post.get("boardId"))
                title = post.get("title") or post.get("subject") or "아이온2 최신 소식"
                
                # HTML 태그 제거
                soup = BeautifulSoup(title, "html.parser")
                title = soup.get_text()

                link = f"https://aion2.plaync.com/ko-kr/board/cm_story/view?articleId={article_id}"
                img_url = post.get("thumbnail") or post.get("imageUrl") or post.get("image") or post.get("representImage")

                if article_id and article_id != "None":
                    articles.append({
                        "id": article_id,
                        "title": title,
                        "link": link,
                        "image": img_url
                    })
            
            if articles:
                return articles
        else:
            print(f"[디버그] API 응답 실패 본문: {response.text[:200]}")

    except Exception as e:
        print(f"[디버그] API 호출 에러: {e}")

    # 2. HTML 백업 크롤링 방식
    try:
        web_url = "https://aion2.plaync.com/ko-kr/board/cm_story/list"
        res = requests.get(web_url, headers=headers, timeout=10)
        print(f"[디버그] HTML 응답 코드: {res.status_code}")
        
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            links = soup.find_all("a")
            for a in links:
                href = a.get("href", "")
                if "articleId=" in href:
                    article_id = href.split("articleId=")[-1].split("&")[0]
                    title = a.get_text(strip=True) or "아이온2 최신 소식"
                    full_link = href if href.startswith("http") else f"https://aion2.plaync.com{href}"
                    
                    img_elem = a.find("img")
                    img_url = img_elem.get("src") if img_elem else None
                    
                    if not any(item["id"] == article_id for item in articles):
                        articles.append({
                            "id": article_id,
                            "title": title,
                            "link": full_link,
                            "image": img_url
                        })
    except Exception as e:
        print(f"[디버그] HTML 파싱 에러: {e}")

    return articles

# ---------------- [5분 주기 자동 체크 루프] ----------------
@tasks.loop(minutes=5)
async def check_aion2_updates():
    global seen_article_ids
    
    channel = bot.get_channel(CHANNEL_ID)
    if not channel:
        return

    articles = await asyncio.to_thread(get_latest_articles)
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
        
        if item["image"]:
            embed.set_image(url=item["image"])
            
        embed.set_footer(text="Aion2 Notification Bot")
        
        await channel.send(embed=embed)
        print(f"새 업데이트 알림 전송 완료: {item['title']}")

# ---------------- [실행] ----------------
keep_alive()
bot.run(TOKEN)

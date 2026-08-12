import os
import re
import sys
import time
import asyncio
import threading
import aiohttp
import discord
from discord.ext import commands, tasks
from flask import Flask

# --------------------------------------------------
# 1. 환경 변수 및 설정
# --------------------------------------------------
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
# Google Generative Language API v1 표준 모델명
GEMINI_MODEL = "gemini-1.5-flash"

# --------------------------------------------------
# 2. Render 24시간 작동용 Flask 웹 서버 설정
# --------------------------------------------------
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# --------------------------------------------------
# 3. 유틸리티 함수 (HTML 태그 제거 및 AI 요약)
# --------------------------------------------------
def clean_html(raw_html):
    if not raw_html:
        return ""
    cleanr = re.compile('<.*?>')
    cleantext = re.sub(cleanr, '', raw_html)
    return cleantext.strip()

async def summarize_with_gemini(title, content):
    if not GEMINI_API_KEY:
        print("[WARN] GEMINI_API_KEY 환경변수가 설정되어 있지 않습니다.", flush=True)
        return None

    start_time = time.time()
    text_to_summarize = clean_html(content)
    if len(text_to_summarize) < 30:
        text_to_summarize = f"제목: {title}"

    # v1beta에서 404 에러가 발생하는 것을 방지하기 위해 v1 엔드포인트 사용
    gemini_url = f"https://generativelanguage.googleapis.com/v1/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    
    prompt = (
        "너는 아이온2 디스코드 알림 봇이야. 아래 게임 공지사항/게시글을 읽고 유저들이 읽기 쉽게 핵심만 3줄로 요약해줘.\n"
        "조건:\n"
        "1. 각 줄은 '- '로 시작할 것\n"
        "2. 군더더기 없이 핵심 변경사항/이벤트 내용만 명확히 요약할 것\n\n"
        f"[제목]: {title}\n"
        f"[내용]: {text_to_summarize[:1500]}"
    )

    payload = {
        "contents": [{"parts": [{"text": prompt}]}]
    }

    req_headers = {
        "Content-Type": "application/json"
    }

    async with aiohttp.ClientSession() as session:
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with session.post(gemini_url, headers=req_headers, json=payload, timeout=timeout) as response:
                elapsed = time.time() - start_time
                if response.status == 200:
                    res_json = await response.json()
                    summary_text = res_json['candidates'][0]['content']['parts'][0]['text']
                    print(f"[INFO] AI 요약 성공 (소요시간: {elapsed:.2f}초)", flush=True)
                    return summary_text.strip()
                else:
                    err_text = await response.text()
                    print(f"[WARN] Gemini API 실패 (코드: {response.status}, 소요시간: {elapsed:.2f}초) - 상세내용: {err_text}", flush=True)
                    return None
        except asyncio.TimeoutError:
            print(f"[WARN] Gemini API 타임아웃 발생 (요약 생략)", flush=True)
            return None
        except Exception as e:
            print(f"[WARN] AI 요약 예외 발생: {e}", flush=True)
            return None

# --------------------------------------------------
# 4. 디스코드 봇 설정 및 이벤트/명령어
# --------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

last_article_id = "6a7b08e4d97eae18cc40d9de"

@bot.event
async def on_ready():
    print(f"[INFO] 디스코드 봇 로그인 성공: {bot.user.name}", flush=True)
    print(f"[INFO] 최초 기준점 저장 (Article ID: {last_article_id})", flush=True)
    if not check_updates.is_running():
        check_updates.start()

@bot.command(name="확인")
async def check_command(ctx):
    await ctx.send("공지사항을 확인하고 요약을 생성하는 중입니다...")
    
    test_title = "아이온2 정기 점검 및 업데이트 안내"
    test_content = "신규 던전 추가 및 클래스 밸런스 패치가 진행됩니다. 서버 점검 시간은 오전 6시부터 10시까지입니다."
    
    summary = await summarize_with_gemini(test_title, test_content)
    if summary:
        embed = discord.Embed(title=f"📢 {test_title}", color=0x00ff00)
        embed.add_field(name="🤖 AI 3줄 요약", value=summary, inline=False)
        await ctx.send(embed=embed)
    else:
        await ctx.send(f"📢 **{test_title}**\n(Gemini 요약 생성에 실패하였습니다.)")

@tasks.loop(minutes=5)
async def check_updates():
    # 주기적인 자동 크롤링 로직 실행 위치
    pass

@check_updates.before_loop
async def before_check_updates():
    await bot.wait_until_ready()

# --------------------------------------------------
# 5. 메인 실행부
# --------------------------------------------------
if __name__ == "__main__":
    # Flask 서버 스레드 시작
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    # 디스코드 봇 실행
    if DISCORD_TOKEN:
        bot.run(DISCORD_TOKEN)
    else:
        print("[ERROR] DISCORD_TOKEN 환경변수가 설정되지 않았습니다.", flush=True)

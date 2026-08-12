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
# 1. 환경 변수 및 설정 (공백 및 따옴표 자동 정돈)
# --------------------------------------------------
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip().strip('"').strip("'")

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

    # API 404 방지를 위해 사용 가능한 Gemini 모델 후보군 순차 시도
    candidate_models = ["gemini-1.5-flash", "gemini-1.5-pro", "gemini-1.0-pro"]

    async with aiohttp.ClientSession() as session:
        for model_name in candidate_models:
            gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={GEMINI_API_KEY}"
            try:
                timeout = aiohttp.ClientTimeout(total=8)
                async with session.post(gemini_url, headers=req_headers, json=payload, timeout=timeout) as response:
                    elapsed = time.time() - start_time
                    if response.status == 200:
                        res_json = await response.json()
                        summary_text = res_json['candidates'][0]['content']['parts'][0]['text']
                        print(f"[INFO] AI 요약 성공 (모델: {model_name}, 소요시간: {elapsed:.2f}초)", flush=True)
                        return summary_text.strip()
                    else:
                        err_text = await response.text()
                        print(f"[WARN] Gemini API 실패 ({model_name}, 코드: {response.status}) - 상세: {err_text}", flush=True)
            except Exception as e:
                print(f"[WARN] Gemini API 호출 예외 ({model_name}): {e}", flush=True)

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
    
    # 요약 실패 여부와 상관없이 항상 깔끔한 임베드 형태로 공지 출력
    if summary:
        embed = discord.Embed(title=f"📢 {test_title}", color=0x00ff00)
        embed.add_field(name="🤖 AI 3줄 요약", value=summary, inline=False)
    else:
        embed = discord.Embed(title=f"📢 {test_title}", color=0xffa500)
        embed.add_field(name="📝 공지 내용", value=test_content, inline=False)
        embed.set_footer(text="⚠️ AI 요약 실패 (Google AI Studio에서 API 키 상태를 확인하세요)")
        
    await ctx.send(embed=embed)

@tasks.loop(minutes=5)
async def check_updates():
    pass

@check_updates.before_loop
async def before_check_updates():
    await bot.wait_until_ready()

# --------------------------------------------------
# 5. 메인 실행부
# --------------------------------------------------
if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    if DISCORD_TOKEN:
        bot.run(DISCORD_TOKEN)
    else:
        print("[ERROR] DISCORD_TOKEN 환경변수가 설정되지 않았습니다.", flush=True)

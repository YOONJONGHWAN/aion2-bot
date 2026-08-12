import os
import re
import sys
import time
import asyncio
import threading
import discord
from discord.ext import commands, tasks
from flask import Flask
from google import genai

# --------------------------------------------------
# 1. 환경 변수 및 구글 AI SDK 설정
# --------------------------------------------------
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip().strip('"').strip("'")

# 최신 구글 GenAI 클라이언트 생성
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

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
# 3. 유틸리티 함수 (HTML 태그 제거 및 상세 AI 요약)
# --------------------------------------------------
def clean_html(raw_html):
    if not raw_html:
        return ""
    cleanr = re.compile('<.*?>')
    cleantext = re.sub(cleanr, '', raw_html)
    return cleantext.strip()

async def summarize_with_gemini(title, content):
    if not GEMINI_API_KEY or not client:
        print("[WARN] GEMINI_API_KEY 환경변수가 설정되어 있지 않습니다.", flush=True)
        return None

    start_time = time.time()
    text_to_summarize = clean_html(content)
    if len(text_to_summarize) < 30:
        text_to_summarize = f"제목: {title}"

    prompt = (
        "너는 아이온2 디스코드 알림 봇이야. 아래 게임 공지사항/게시글을 읽고 유저들이 꼭 알아야 할 중요한 내용을 알차게 정리해줘.\n"
        "조건:\n"
        "1. 줄 수에 구애받지 말고, 주요 점검 시간, 핵심 변경사항, 신규 이벤트, 보상, 주의사항 등 중요한 정보가 빠짐없이 포함되도록 작성할 것\n"
        "2. 유저들이 읽기 쉽게 각 항목은 '- '로 시작하여 가독성 높게 정리할 것\n"
        "3. 군더더기 서론이나 인사말 없이 핵심 요약 내용만 출력할 것\n\n"
        f"[제목]: {title}\n"
        f"[내용]: {text_to_summarize[:2500]}"
    )

    candidate_models = ['gemini-3.5-flash', 'gemini-3.1-flash-lite', 'gemini-3.5-flash-lite']
    loop = asyncio.get_running_loop()

    for model_name in candidate_models:
        try:
            # 10초 타임아웃 설정 (지연 방지)
            def call_api(m=model_name):
                return client.models.generate_content(model=m, contents=prompt)

            response = await asyncio.wait_for(
                loop.run_in_executor(None, call_api),
                timeout=10.0
            )

            if response and response.text:
                elapsed = time.time() - start_time
                print(f"[INFO] AI 요약 성공 (사용 모델: {model_name}, 소요시간: {elapsed:.2f}초)", flush=True)
                return response.text.strip()

        except asyncio.TimeoutError:
            print(f"[WARN] {model_name} 모델 10초 타임아웃 초과, 다음 모델 시도 중...", flush=True)
        except Exception as e:
            print(f"[WARN] {model_name} 모델 호출 실패, 다음 모델 시도 중... (사유: {e})", flush=True)

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
    test_content = (
        "신규 던전 '파멸의 신전'이 추가되며 클래스 밸런스 패치가 진행됩니다. "
        "서버 점검 시간은 오전 6시부터 10시까지 총 4시간 동안 진행되며, "
        "점검 보상으로 신성한 수호석 10개와 경험치 부스터가 지급됩니다. "
        "점검 전 캐릭터를 안전한 장소로 이동시켜 주시기 바랍니다."
    )
    test_url = "https://aion2.plaync.com/ko-kr/board/cm_story/list" 
    
    # !확인 명령어로 테스트할 때 노출시킬 샘플 이미지 URL (원래 크롤링 시에는 게시글의 진짜 썸네일 URL 입력)
    test_image_url = "https://f2.plaync.com/aion2/v2/og_image.png"

    summary = await summarize_with_gemini(test_title, test_content)
    
    embed = discord.Embed(
        title=f"📢 {test_title}", 
        url=test_url, 
        color=0x00ff00
    )
    
    if summary:
        embed.add_field(name="🤖 AI 주요 내용 요약", value=summary, inline=False)
    else:
        embed.add_field(name="📝 공지 내용", value=test_content, inline=False)
        embed.set_footer(text="⚠️ AI 요약 생성 실패 (Render 로그를 확인해 주세요)")
    
    embed.add_field(name="🔗 공지 바로가기", value=f"[공지사항 전체보기]({test_url})", inline=False)
    
    # 썸네일 이미지 설정
    if test_image_url:
        embed.set_image(url=test_image_url)
        
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

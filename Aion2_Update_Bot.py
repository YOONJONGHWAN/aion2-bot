import os

# 💡 [핵심] Render가 런타임 시 환경변수를 /opt/render로 덮어쓰는 것을 파이썬 실행 직후 바로 강제 재설정
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "/app/ms-playwright"

import asyncio
import logging
import glob
from flask import Flask
from threading import Thread
import discord
from discord.ext import commands, tasks
import httpx
from google import genai
from google.genai import types
from playwright.async_api import async_playwright

# ... (중략: 로깅, 환경변수, Flask, 봇 설정 부분 동일) ...

# 렌더 환경 브라우저 실행 경로 에러 방지용 함수
async def launch_browser(p):
    custom_path = "/app/ms-playwright"
    
    # headless-shell과 일반 chrome 실행 파일 탐색
    search_patterns = [
        f"{custom_path}/**/chrome-headless-shell*/chrome-headless-shell",
        f"{custom_path}/**/chrome-linux*/chrome",
    ]
    
    found_executables = []
    for pattern in search_patterns:
        found_executables = glob.glob(pattern, recursive=True)
        if found_executables:
            break
            
    args = [
        "--no-sandbox", 
        "--disable-setuid-sandbox", 
        "--disable-dev-shm-usage", 
        "--disable-gpu",
        "--disable-blink-features=AutomationControlled",
        "--window-size=1920,1080"
    ]
    
    if found_executables:
        logging.info(f"성공! 크롬 실행파일을 찾았습니다: {found_executables[0]}")
        return await p.chromium.launch(headless=True, executable_path=found_executables[0], args=args)
    else:
        logging.warning("지정한 경로에서 크롬을 못 찾았습니다. 기본 경로로 시도합니다.")
        return await p.chromium.launch(headless=True, args=args)

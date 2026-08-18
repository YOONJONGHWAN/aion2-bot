async def check_new_notices(is_initial=False):
    global known_notices
    new_notices_found = []
    
    async with async_playwright() as p:
        browser_executable_path = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH")
        launch_kwargs = {
            "headless": True, 
            "args": [
                "--no-sandbox", 
                "--disable-setuid-sandbox", 
                "--disable-dev-shm-usage", 
                "--disable-gpu",
                "--disable-blink-features=AutomationControlled",
                "--window-size=1920,1080"
            ]
        }
        if browser_executable_path:
            launch_kwargs["executable_path"] = browser_executable_path
            
        browser = await p.chromium.launch(**launch_kwargs)
        
        # 일반 유저처럼 보이도록 설정
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="ko-KR",
            timezone_id="Asia/Seoul"
        )
        
        # 💡 핵심: 클라우드플레어 등 봇 탐지를 우회하기 위해 webdriver 속성 제거
        await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        
        page = await context.new_page()
        
        try:
            logging.info(f"페이지 접속 시작: {TARGET_URL}")
            # 차단을 우회하기 위해 지연 시간을 두고 접속
            await page.goto(TARGET_URL, timeout=DETAIL_TIMEOUT, wait_until="domcontentloaded")
            
            # 자바스크립트가 차단 검사를 마치고 본문을 렌더링할 때까지 충분히 대기
            await page.wait_for_timeout(7000)
            
            page_title = await page.title()
            logging.info(f"현재 페이지 타이틀: {page_title}")
            
            content_snippet = await page.content()
            logging.info(f"페이지 HTML 일부: {content_snippet[:300]}...")

            articles = await page.query_selector_all("a.title")
            logging.info(f"페이지 내 발견된 공지 제목 링크 개수: {len(articles)}")
            
            current_notices = []
            for article in articles:
                href = await article.get_attribute("href")
                text = await article.inner_text()
                
                if href and "cm_story/view" in href:
                    if not href.startswith("http"):
                        href = "https://aion2.plaync.com" + href
                    
                    clean_title = text.strip()
                    if clean_title and href not in [n[1] for n in current_notices]:
                        current_notices.append((clean_title, href))
            
            logging.info(f"유효하게 파싱된 공지 개수: {len(current_notices)}")
            
            if is_initial:
                for title, link in current_notices:
                    known_notices.add(link)
                logging.info(f"동기화 완료: 총 {len(known_notices)}개의 유효 공지 확인됨")
            else:
                for title, link in current_notices:
                    if link not in known_notices:
                        known_notices.add(link)
                        image_urls = await fetch_article_images(page, link)
                        summary = await generate_ai_summary_from_images(title, image_urls)
                        new_notices_found.append((title, link, summary, image_urls))
                        
        except Exception as e:
            logging.warning(f"공지 확인 중 에러 발생: {e}")
        finally:
            await browser.close()
            
    return new_notices_found

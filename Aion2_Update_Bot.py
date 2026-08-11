import requests
from bs4 import BeautifulSoup

# 정확한 대상 URL: cm_story
URL = "https://aion2.plaync.com/ko-kr/board/cm_story/list"

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
}

try:
    response = requests.get(URL, headers=headers, timeout=10)
    print("1. 응답 상태 코드:", response.status_code)
    print("2. 최종 이동 URL :", response.url)
    
    soup = BeautifulSoup(response.text, 'html.parser')
    
    # cm_story 게시글 링크 탐색
    links = soup.select('a[href*="/board/cm_story/view"]')
    print(f"3. 찾은 cm_story 게시글 수: {len(links)}개")
    
    print("\n--- [상위 5개 추출 결과] ---")
    for idx, a in enumerate(links[:5], 1):
        title = a.get_text().strip()
        href = a['href']
        print(f"{idx}. {title} -> {href}")

except Exception as e:
    print("오류 발생:", e)

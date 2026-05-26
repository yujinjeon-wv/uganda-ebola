"""
외교부 해외안전여행 - 우간다 안전공지 크롤러
GitHub Actions에서 주기적으로 실행되어 data/posts.json을 업데이트합니다.
"""

import requests
import json
import os
import re
import time
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser

LIST_API   = "https://www.0404.go.kr/util/getSafetyTravelNtcList"
DETAIL_API = "https://www.0404.go.kr/util/getSafetyTravelNtcDetail"
UGANDA_NTN_CD = "166"
OUTPUT_FILE   = "data/posts.json"
KST = timezone(timedelta(hours=9))

HEADERS = {
    "Content-Type": "application/json;charset=UTF-8",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.0404.go.kr",
    "Referer": "https://www.0404.go.kr/bbs/safetyNtc/list",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}

# ── HTML → plain text ──────────────────────────────────────────────────
class HTMLStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text_parts = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip = True
        if tag == "br":
            self.text_parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = False
        if tag in ("p", "div", "li", "tr"):
            self.text_parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.text_parts.append(data)

def strip_html(html_str):
    if not html_str:
        return ""
    parser = HTMLStripper()
    parser.feed(html_str)
    text = "".join(parser.text_parts)
    # 비상연락처 이후 내용 제거
    for kw in ["비상연락처", "긴급연락처", "영사콜센터", "☎", "※ 한국에서"]:
        idx = text.find(kw)
        if idx != -1:
            text = text[:idx]
    # 연속 공백·줄바꿈 정리
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()

# ── 목록 API ──────────────────────────────────────────────────────────
def fetch_list():
    res = requests.post(LIST_API, headers=HEADERS, json={"pageSize": 200}, timeout=15)
    res.raise_for_status()
    return res.json().get("data", [])

# ── 상세 API (본문 크롤링) ────────────────────────────────────────────
def fetch_detail(pst_no, pst_type):
    try:
        res = requests.post(
            DETAIL_API,
            headers=HEADERS,
            json={"pstNo": pst_no, "pstType": pst_type},
            timeout=15,
        )
        if not res.ok:
            return ""
        data = res.json()
        detail = data.get("data") or {}
        html_content = detail.get("cnHtml") or detail.get("cnTxt") or ""
        return strip_html(html_content)
    except Exception as e:
        print(f"    상세 API 실패 ({pst_no}): {e}")
        return ""

# ── 필터 ─────────────────────────────────────────────────────────────
def is_uganda(post):
    return (
        post.get("ntnCd") == UGANDA_NTN_CD
        or post.get("ntnNm") == "우간다"
        or "우간다" in (post.get("ttlNm") or "")
    )

def format_date(reg_dt):
    if not reg_dt or len(reg_dt) < 3:
        return ""
    try:
        return f"{reg_dt[0]}-{reg_dt[1]:02d}-{reg_dt[2]:02d}"
    except Exception:
        return ""

def get_detail_url(post):
    type_path = {
        "safetyNtc": "safetyNtc",
        "embsyNtc":  "safetyNtc",
        "travelAlertAjmt": "travelAlert",
    }
    path = type_path.get(post.get("pstType", ""), "safetyNtc")
    return f"https://www.0404.go.kr/bbs/{path}/detail/{post.get('pstNo', '')}"

# ── Gemini 요약 ───────────────────────────────────────────────────────
def summarize_with_gemini(title, body, pst_type_nm, date, api_key):
    if not api_key:
        return ""
    content = body[:1500] if body else f"(본문 없음 — 제목: {title})"
    prompt = (
        f"다음은 외교부 해외안전여행의 우간다 안전공지입니다.\n\n"
        f"제목: {title}\n"
        f"유형: {pst_type_nm}\n"
        f"날짜: {date}\n"
        f"본문:\n{content}\n\n"
        f"위 공지의 핵심 내용을 3~5문장으로 요약해주세요. "
        f"독자는 6~7월 우간다 방문을 앞두고 방문 여부를 결정해야 하는 한국인입니다. "
        f"에볼라 관련 위험도, 구체적 발생 지역, 권고 행동 지침을 중심으로 써주세요. "
        f"비상연락처나 일반적 행정 안내는 생략하세요. "
        f"마크다운 없이 plain text로만 답변하세요."
    )
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.5-flash-preview:generateContent?key={api_key}"
    )
    try:
        res = requests.post(
            url,
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"maxOutputTokens": 300, "temperature": 0.2},
            },
            timeout=25,
        )
        if res.ok:
            data = res.json()
            return (
                data.get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [{}])[0]
                .get("text", "")
                .strip()
            )
    except Exception as e:
        print(f"    Gemini 요약 실패: {e}")
    return ""

# ── 기존 데이터 로드 ──────────────────────────────────────────────────
def load_existing():
    if os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"updated_at": "", "posts": []}

# ── 메인 ─────────────────────────────────────────────────────────────
def run():
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    print(f"Gemini API 키: {'있음' if gemini_key else '없음 (요약 생략)'}")

    print("외교부 목록 API 호출 중...")
    all_posts = fetch_list()
    uganda_posts = [p for p in all_posts if is_uganda(p)]
    print(f"우간다 게시글: {len(uganda_posts)}건")

    existing = load_existing()
    existing_map = {p["id"]: p for p in existing.get("posts", [])}

    results = []
    new_count = 0

    for p in uganda_posts:
        post_id   = str(p.get("pstNo", ""))
        pst_type  = p.get("pstType", "")
        title     = p.get("ttlNm", "")
        date      = format_date(p.get("regDt", []))
        is_new    = post_id not in existing_map

        # 기존 데이터 재사용
        cached    = existing_map.get(post_id, {})
        body      = cached.get("body", "")
        summary   = cached.get("summary", "")

        # 새 글이면 본문 + 요약 생성
        if is_new:
            new_count += 1
            print(f"  [{new_count}] 새 글: {title[:40]}")
            body = fetch_detail(post_id, pst_type)
            time.sleep(0.5)  # API 부하 방지
            if gemini_key:
                print(f"      → Gemini 요약 생성 중...")
                summary = summarize_with_gemini(
                    title, body, p.get("pstTypeNm", ""), date, gemini_key
                )
                time.sleep(0.5)

        results.append({
            "id":        post_id,
            "title":     title,
            "type":      pst_type,
            "type_name": p.get("pstTypeNm", ""),
            "date":      date,
            "url":       get_detail_url(p),
            "body":      body,
            "summary":   summary,
            "is_new":    is_new,
        })

    now_kst = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    output  = {"updated_at": now_kst, "total": len(results), "posts": results}

    os.makedirs("data", exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n저장 완료 → {OUTPUT_FILE}")
    print(f"전체: {len(results)}건 / 신규: {new_count}건 / 업데이트: {now_kst}")

if __name__ == "__main__":
    run()

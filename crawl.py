import requests
import json
import os
import re
import time
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser

LIST_API      = "https://www.0404.go.kr/util/getSafetyTravelNtcList"
UGANDA_NTN_CD = "166"
OUTPUT_FILE   = "data/posts.json"
KST = timezone(timedelta(hours=9))

HEADERS = {
    "Content-Type": "application/json;charset=UTF-8",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.0404.go.kr",
    "Referer": "https://www.0404.go.kr/bbs/safetyNtc/list",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}

# ── 날짜: 제목에서 파싱 (5.23) → 2026-05-23 ─────────────────────────
def parse_date_from_title(title, fallback_reg_dt):
    # "(5.23)", "(5.20)" 등 패턴 추출
    m = re.search(r'\((\d{1,2})\.(\d{1,2})\)\s*$', title.strip())
    if m:
        month = int(m.group(1))
        day   = int(m.group(2))
        year  = 2026
        return f"{year}-{month:02d}-{day:02d}"
    # fallback: regDt 배열
    if fallback_reg_dt and len(fallback_reg_dt) >= 3:
        try: return f"{fallback_reg_dt[0]}-{fallback_reg_dt[1]:02d}-{fallback_reg_dt[2]:02d}"
        except: pass
    return ""

# ── 본문: POST API로 가져오기 ─────────────────────────────────────────
class HTMLStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self._skip = False
    def handle_starttag(self, tag, attrs):
        if tag in ("script","style"): self._skip = True
        if tag == "br": self.parts.append("\n")
    def handle_endtag(self, tag):
        if tag in ("script","style"): self._skip = False
        if tag in ("p","div","li","tr"): self.parts.append("\n")
    def handle_data(self, data):
        if not self._skip: self.parts.append(data)

def strip_html(html_str):
    if not html_str: return ""
    p = HTMLStripper(); p.feed(html_str)
    text = "".join(p.parts)
    for kw in ["비상연락처","긴급연락처","영사콜센터","☎","※ 한국에서","문의처"]:
        idx = text.find(kw)
        if idx != -1: text = text[:idx]
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()

def fetch_detail_body(pst_no, pst_type):
    """여러 API 엔드포인트 시도"""
    endpoints = [
        ("POST", "https://www.0404.go.kr/util/getSafetyTravelNtcDetail",
         {"pstNo": pst_no, "pstType": pst_type}),
        ("POST", "https://www.0404.go.kr/util/getSafetyNtcDetail",
         {"pstNo": pst_no}),
        ("POST", "https://www.0404.go.kr/util/getTravelAlertDetail",
         {"pstNo": pst_no}),
    ]
    for method, url, payload in endpoints:
        try:
            res = requests.post(url, headers=HEADERS, json=payload, timeout=12)
            if not res.ok: continue
            data = res.json()
            detail = data.get("data") or {}
            if isinstance(detail, list) and detail:
                detail = detail[0]
            html = detail.get("cnHtml") or detail.get("cnTxt") or detail.get("content") or ""
            if html:
                body = strip_html(html)
                if len(body) > 50:
                    print(f"    본문 획득 ({url.split('/')[-1]}): {len(body)}자")
                    return body[:3000]
        except Exception as e:
            print(f"    엔드포인트 실패 ({url.split('/')[-1]}): {e}")
        time.sleep(0.3)
    print(f"    본문 없음 — 제목 기반으로 요약 진행")
    return ""

# ── Gemini 요약 ───────────────────────────────────────────────────────
GEMINI_MODELS = [
    "gemini-2.0-flash",
    "gemini-1.5-flash",
    "gemini-1.5-pro",
]

def summarize_with_gemini(title, body, pst_type_nm, date, api_key):
    if not api_key: return ""
    content = body[:2000] if body else f"제목: {title}"
    prompt = (
        f"다음은 외교부 해외안전여행의 우간다 안전공지입니다.\n\n"
        f"제목: {title}\n유형: {pst_type_nm}\n날짜: {date}\n본문:\n{content}\n\n"
        f"위 공지의 핵심 내용을 3~5문장으로 요약해주세요. "
        f"독자는 6~7월 우간다 방문을 앞두고 방문 여부를 결정해야 하는 한국인입니다. "
        f"에볼라 위험도, 발생 지역, 권고 행동 지침을 중심으로 써주세요. "
        f"비상연락처·행정 안내는 생략하세요. "
        f"마크다운 없이 plain text로만 답변하세요."
    )
    for model in GEMINI_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        try:
            res = requests.post(url, json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"maxOutputTokens": 300, "temperature": 0.2}
            }, timeout=25)
            if res.ok:
                text = res.json().get("candidates",[{}])[0].get("content",{}).get("parts",[{}])[0].get("text","").strip()
                if text:
                    print(f"    Gemini({model}) 요약 완료")
                    return text
            else:
                print(f"    Gemini({model}) 오류: {res.status_code}")
        except Exception as e:
            print(f"    Gemini({model}) 실패: {e}")
        time.sleep(0.5)
    return ""

# ── 유틸 ─────────────────────────────────────────────────────────────
def fetch_list():
    res = requests.post(LIST_API, headers=HEADERS, json={"pageSize": 200}, timeout=15)
    res.raise_for_status()
    return res.json().get("data", [])

def is_uganda(post):
    return (post.get("ntnCd") == UGANDA_NTN_CD
            or post.get("ntnNm") == "우간다"
            or "우간다" in (post.get("ttlNm") or ""))

def get_detail_url(post):
    type_path = {"safetyNtc":"safetyNtc","embsyNtc":"safetyNtc","travelAlertAjmt":"travelAlert"}
    path = type_path.get(post.get("pstType",""), "safetyNtc")
    return f"https://www.0404.go.kr/bbs/{path}/detail/{post.get('pstNo','')}"

def load_existing():
    if os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE,"r",encoding="utf-8") as f: return json.load(f)
    return {"updated_at":"","posts":[]}

# ── 메인 ─────────────────────────────────────────────────────────────
def run():
    gemini_key = os.environ.get("GEMINI_API_KEY","")
    print(f"Gemini API 키: {'있음' if gemini_key else '없음'}")
    print("외교부 목록 API 호출 중...")
    all_posts = fetch_list()
    uganda_posts = [p for p in all_posts if is_uganda(p)]
    print(f"우간다 게시글: {len(uganda_posts)}건")

    existing     = load_existing()
    existing_map = {p["id"]: p for p in existing.get("posts",[])}
    results      = []
    new_count    = 0

    for p in uganda_posts:
        post_id  = str(p.get("pstNo",""))
        pst_type = p.get("pstType","")
        title    = p.get("ttlNm","")
        date     = parse_date_from_title(title, p.get("regDt",[]))
        is_new   = post_id not in existing_map
        cached   = existing_map.get(post_id,{})
        body     = cached.get("body","")
        summary  = cached.get("summary","")

        if is_new or not summary:
            if is_new: new_count += 1
            print(f"\n  글: {title[:45]}")
            print(f"    날짜: {date}")
            if not body:
                body = fetch_detail_body(post_id, pst_type)
            if gemini_key:
                print(f"    → Gemini 요약 생성 중...")
                summary = summarize_with_gemini(title, body, p.get("pstTypeNm",""), date, gemini_key)
            time.sleep(0.5)

        results.append({
            "id": post_id, "title": title,
            "type": pst_type, "type_name": p.get("pstTypeNm",""),
            "date": date, "url": get_detail_url(p),
            "body": body, "summary": summary, "is_new": is_new,
        })

    now_kst = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    output  = {"updated_at": now_kst, "total": len(results), "posts": results}
    os.makedirs("data", exist_ok=True)
    with open(OUTPUT_FILE,"w",encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n완료: {len(results)}건 / 신규: {new_count}건 / {now_kst}")

if __name__ == "__main__":
    run()

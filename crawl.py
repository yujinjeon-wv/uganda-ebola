"""
외교부 해외안전여행 - 우간다 안전공지 크롤러
GitHub Actions에서 주기적으로 실행되어 data/posts.json을 업데이트합니다.
"""

import requests
import json
import os
from datetime import datetime, timezone, timedelta

API_URL = "https://www.0404.go.kr/util/getSafetyTravelNtcList"
UGANDA_NTN_CD = "166"
OUTPUT_FILE = "data/posts.json"

KST = timezone(timedelta(hours=9))

HEADERS = {
    "Content-Type": "application/json;charset=UTF-8",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.0404.go.kr",
    "Referer": "https://www.0404.go.kr/bbs/safetyNtc/list",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}


def fetch_posts():
    res = requests.post(
        API_URL,
        headers=HEADERS,
        json={"pageSize": 200},
        timeout=15,
    )
    res.raise_for_status()
    data = res.json()
    return data.get("data", [])


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
        "embsyNtc": "safetyNtc",
        "travelAlertAjmt": "travelAlert",
    }
    path = type_path.get(post.get("pstType", ""), "safetyNtc")
    return f"https://www.0404.go.kr/bbs/{path}/detail/{post.get('pstNo', '')}"


def summarize_with_gemini(post, api_key):
    """Gemini API로 게시글 제목 기반 요약 생성"""
    if not api_key:
        return ""

    prompt = (
        f"다음은 외교부 해외안전여행 사이트의 우간다 관련 안전공지입니다.\n\n"
        f"제목: {post.get('ttlNm', '')}\n"
        f"유형: {post.get('pstTypeNm', '')}\n"
        f"날짜: {format_date(post.get('regDt', []))}\n\n"
        f"이 공지의 핵심 내용을 2~3문장으로 간결하게 요약해주세요. "
        f"독자는 우간다 방문을 앞둔 한국인입니다. "
        f"실무적으로 중요한 행동 지침이 있다면 포함해주세요. "
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
                "generationConfig": {"maxOutputTokens": 200, "temperature": 0.3},
            },
            timeout=20,
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
        print(f"  Gemini 요약 실패 ({post.get('pstNo')}): {e}")
    return ""


def load_existing():
    if os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"updated_at": "", "posts": []}


def run():
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    print(f"Gemini API 키: {'있음' if gemini_key else '없음 (요약 생략)'}")

    print("외교부 API 호출 중...")
    all_posts = fetch_posts()
    uganda_posts = [p for p in all_posts if is_uganda(p)]
    print(f"우간다 게시글: {len(uganda_posts)}건")

    existing = load_existing()
    existing_ids = {p["id"] for p in existing.get("posts", [])}

    results = []
    new_count = 0

    for p in uganda_posts:
        post_id = str(p.get("pstNo", ""))
        is_new = post_id not in existing_ids

        # 기존 요약 재사용
        existing_summary = ""
        for ep in existing.get("posts", []):
            if ep["id"] == post_id:
                existing_summary = ep.get("summary", "")
                break

        # 새 글이고 Gemini 키 있으면 요약 생성
        summary = existing_summary
        if is_new and gemini_key and not existing_summary:
            print(f"  요약 생성: {p.get('ttlNm', '')[:30]}...")
            summary = summarize_with_gemini(p, gemini_key)

        if is_new:
            new_count += 1

        results.append({
            "id": post_id,
            "title": p.get("ttlNm", ""),
            "type": p.get("pstType", ""),
            "type_name": p.get("pstTypeNm", ""),
            "date": format_date(p.get("regDt", [])),
            "url": get_detail_url(p),
            "summary": summary,
            "is_new": is_new,
        })

    now_kst = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    output = {
        "updated_at": now_kst,
        "total": len(results),
        "posts": results,
    }

    os.makedirs("data", exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"저장 완료 → {OUTPUT_FILE}")
    print(f"전체: {len(results)}건 / 신규: {new_count}건 / 업데이트: {now_kst}")


if __name__ == "__main__":
    run()

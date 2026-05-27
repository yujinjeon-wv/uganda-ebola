import requests
import json
import os
import re
import time
import glob
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup
from pypdf import PdfReader
from docx import Document

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
GET_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://www.0404.go.kr/bbs/safetyNtc/list",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# ── Gemini 요약 ───────────────────────────────────────────────────────
GEMINI_MODELS = ["gemini-2.0-flash", "gemini-2.5-flash"]

def summarize_with_gemini(title, content, source_type, date, api_key):
    if not api_key: return ""
    if source_type == "gc":
        prompt = (
            f"다음은 World Vision 본부에서 발행한 Global Advisory (내부 공문)입니다.\n\n"
            f"제목: {title}\n날짜: {date}\n내용:\n{content[:2500]}\n\n"
            f"위 공문의 핵심 내용을 한국어로 4~6문장으로 요약해주세요.\n"
            f"여행 스탠스(어느 지역 여행이 금지/제한되는지), 현지 직원 지침, 주요 건강 예방 수칙을 중심으로 써주세요.\n"
            f"불렛포인트(•) 항목으로 간결하게 답변하세요. 마크다운 없이 plain text로만 답변하세요."
        )
    elif source_type == "no":
        prompt = (
            f"다음은 우간다 현지에서 작성된 업데이트 내용입니다.\n\n"
            f"제목: {title}\n날짜: {date}\n내용:\n{content[:2500]}\n\n"
            f"위 내용을 한국어로 4~6문장으로 요약해주세요.\n"
            f"현재 상황, 확진자 수, 권고 행동 지침을 중심으로 써주세요.\n"
            f"불렛포인트(•) 항목으로 간결하게 답변하세요. 마크다운 없이 plain text로만 답변하세요."
        )
    else:
        prompt = (
            f"다음은 외교부 해외안전여행의 우간다 안전공지입니다.\n\n"
            f"제목: {title}\n날짜: {date}\n본문:\n{content[:2500]}\n\n"
            f"위 공지의 핵심 내용을 4~6문장으로 요약해주세요.\n"
            f"확진자 수, 발생 지역, 권고 행동 지침을 중심으로 써주세요.\n"
            f"비상연락처, 행정 안내는 생략하세요.\n"
            f"불렛포인트(•) 항목으로 간결하게 답변하세요. 마크다운 없이 plain text로만 답변하세요."
        )
    for model in GEMINI_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        try:
            res = requests.post(url, json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"maxOutputTokens": 1024, "temperature": 0.2, "thinkingConfig": {"thinkingBudget": 0}}
            }, timeout=30)
            if res.ok:
                text = res.json().get("candidates",[{}])[0].get("content",{}).get("parts",[{}])[0].get("text","").strip()
                if text:
                    print(f"    Gemini({model}) 요약 완료: {len(text)}자")
                    return text
            else:
                print(f"    Gemini({model}) 오류: {res.status_code}")
        except Exception as e:
            print(f"    Gemini({model}) 실패: {e}")
        time.sleep(2)
    return ""

# ── 파일 읽기 ─────────────────────────────────────────────────────────
def read_pdf(filepath):
    try:
        reader = PdfReader(filepath)
        text = ""
        for page in reader.pages:
            text += page.extract_text() or ""
        return text.strip()
    except Exception as e:
        print(f"    PDF 읽기 실패: {e}")
        return ""

def read_docx(filepath):
    try:
        doc = Document(filepath)
        text = "\n".join([p.text for p in doc.paragraphs if p.text.strip()])
        return text.strip()
    except Exception as e:
        print(f"    DOCX 읽기 실패: {e}")
        return ""

def read_txt(filepath):
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception as e:
        print(f"    TXT 읽기 실패: {e}")
        return ""

# ── 외교부 크롤링 ─────────────────────────────────────────────────────
def parse_date_from_title(title, fallback_reg_dt):
    m = re.search(r'\((\d{1,2})\.(\d{1,2})\)\s*$', title.strip())
    if m:
        return f"2026-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    if fallback_reg_dt and len(fallback_reg_dt) >= 3:
        try: return f"{fallback_reg_dt[0]}-{fallback_reg_dt[1]:02d}-{fallback_reg_dt[2]:02d}"
        except: pass
    return ""

def fetch_detail_body(pst_no, pst_type):
    type_path = {
        "safetyNtc": "safetyNtc",
        "embsyNtc": "safetyNtc",
        "travelAlertAjmt": "travelAlertAjmt",
    }
    path = type_path.get(pst_type, "safetyNtc")
    url = f"https://www.0404.go.kr/bbs/{path}/{pst_no}/detail"
    try:
        res = requests.get(url, headers=GET_HEADERS, timeout=15)
        print(f"    상세 페이지 {res.status_code}: {url}")
        if not res.ok: return ""
        soup = BeautifulSoup(res.text, "html.parser")
        for tag in soup(["script","style","header","footer","nav","noscript"]): tag.decompose()
        paragraphs = soup.find_all("p")
        lines = []
        for p in paragraphs:
            text = p.get_text(separator=" ").strip()
            text = re.sub(r"\s+", " ", text)
            if len(text) > 5: lines.append(text)
        body = "\n".join(lines)
        for kw in ["비상연락처","긴급연락처","영사콜센터","☎","문의처","※ 한국에서"]:
            idx = body.find(kw)
            if idx != -1: body = body[:idx]
        body = body.strip()
        print(f"    본문 획득: {len(body)}자")
        return body[:4000]
    except Exception as e:
        print(f"    상세 페이지 실패: {e}")
        return ""

def fetch_list():
    all_posts = []
    # 전체 목록
    res = requests.post(LIST_API, headers=HEADERS, json={"pageSize": 500}, timeout=15)
    res.raise_for_status()
    all_posts += res.json().get("data", [])
    # 우간다 국가코드로 추가 호출
    res2 = requests.post(LIST_API, headers=HEADERS, json={"pageSize": 100, "ntnCd": "166"}, timeout=15)
    if res2.ok:
        all_posts += res2.json().get("data", [])
    # 중복 제거
    seen = set()
    result = []
    for p in all_posts:
        if p.get("pstNo") not in seen:
            seen.add(p.get("pstNo"))
            result.append(p)
    return result

def is_uganda(post):
    return (post.get("ntnCd") == UGANDA_NTN_CD
            or post.get("ntnNm") == "우간다"
            or "우간다" in (post.get("ttlNm") or ""))

def get_detail_url(post):
    type_path = {"safetyNtc":"safetyNtc","embsyNtc":"safetyNtc","travelAlertAjmt":"travelAlertAjmt"}
    path = type_path.get(post.get("pstType",""), "safetyNtc")
    return f"https://www.0404.go.kr/bbs/{path}/{post.get('pstNo','')}/detail"

# ── GC 업데이트 (PDF) ─────────────────────────────────────────────────
def load_gc_posts(gemini_key, existing_map):
    results = []
    gc_files = glob.glob("data/gc/*.pdf")
    for filepath in sorted(gc_files):
        filename = os.path.basename(filepath)
        post_id  = f"gc_{filename}"
        m = re.search(r"(\d{4})[-_](\d{2})[-_](\d{2})", filename)
        date  = f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""
        title = f"[GC] {filename.replace('.pdf','').replace('_',' ')}"
        cached  = existing_map.get(post_id, {})
        body    = cached.get("body", "")
        summary = cached.get("summary", "")
        if not body:
            print(f"\n  GC PDF: {filename}")
            body = read_pdf(filepath)
            print(f"    본문 획득: {len(body)}자")
            if gemini_key and body:
                print(f"    → Gemini 요약 생성 중...")
                summary = summarize_with_gemini(title, body, "gc", date, gemini_key)
            time.sleep(60)
        results.append({
            "id": post_id, "title": title,
            "type": "gc", "type_name": "GC 업데이트",
            "date": date, "url": "",
            "body": body, "summary": summary,
            "is_new": post_id not in existing_map,
        })
    return results

# ── NO 업데이트 (TXT / DOCX) ─────────────────────────────────────────
def parse_no_txt(filepath):
    raw = read_txt(filepath)
    lines = raw.split("\n")
    meta = {}
    content_lines = []
    in_content = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("date:"):
            meta["date"] = stripped.replace("date:","").strip()
        elif stripped.startswith("title:"):
            meta["title"] = stripped.replace("title:","").strip()
        elif stripped.startswith("content:"):
            in_content = True
        elif in_content:
            content_lines.append(line)
    return meta.get("date",""), meta.get("title",""), "\n".join(content_lines).strip()

def load_no_posts(gemini_key, existing_map):
    results = []
    # txt + docx 둘 다 처리
    no_files = sorted(
        glob.glob("data/no/*.txt") + glob.glob("data/no/*.docx"),
        reverse=True
    )
    for filepath in no_files:
        filename = os.path.basename(filepath)
        post_id  = f"no_{filename}"
        cached   = existing_map.get(post_id, {})
        body     = cached.get("body", "")
        summary  = cached.get("summary", "")
        date, title = "", filename.replace(".txt","").replace(".docx","").replace("_"," ")

        if not body:
            print(f"\n  NO 업데이트: {filename}")
            try:
                if filename.endswith(".txt"):
                    date, title, body = parse_no_txt(filepath)
                elif filename.endswith(".docx"):
                    body = read_docx(filepath)
                    # 날짜: 파일명에서 추출
                    m = re.search(r"(\d{4})[-_](\d{2})[-_](\d{2})", filename)
                    if m:
                        date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
                    # 날짜: 본문 첫 줄에서도 시도
                    if not date:
                        m2 = re.search(r"Date:\s*(\d+)\s+(\w+)\s+(\d{4})", body)
                        if m2:
                            months = {"January":"01","February":"02","March":"03","April":"04","May":"05","June":"06","July":"07","August":"08","September":"09","October":"10","November":"11","December":"12"}
                            mon = months.get(m2.group(2), "01")
                            date = f"{m2.group(3)}-{mon}-{int(m2.group(1)):02d}"
                print(f"    본문 획득: {len(body)}자")
                if gemini_key and body:
                    print(f"    → Gemini 요약 생성 중...")
                    summary = summarize_with_gemini(title, body, "no", date, gemini_key)
                time.sleep(60)
            except Exception as e:
                print(f"    NO 파일 읽기 실패: {e}")

        results.append({
            "id": post_id, "title": title,
            "type": "no", "type_name": "NO 업데이트",
            "date": date, "url": "",
            "body": body, "summary": summary,
            "is_new": post_id not in existing_map,
        })
    return results

# ── 데이터 로드/저장 ──────────────────────────────────────────────────
def load_existing():
    if os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE,"r",encoding="utf-8") as f: return json.load(f)
    return {"updated_at":"","posts":[]}

# ── 메인 ─────────────────────────────────────────────────────────────
def run():
    gemini_key = os.environ.get("GEMINI_API_KEY","")
    print(f"Gemini API 키: {'있음' if gemini_key else '없음'}")

    existing     = load_existing()
    existing_map = {p["id"]: p for p in existing.get("posts",[])}

    # 1. 외교부 공지
    print("\n=== 외교부 공지 크롤링 ===")
    all_posts    = fetch_list()
    uganda_posts = [p for p in all_posts if is_uganda(p)]
    print(f"우간다 게시글: {len(uganda_posts)}건")

    mofa_results = []
    for p in uganda_posts:
        post_id  = str(p.get("pstNo",""))
        pst_type = p.get("pstType","")
        title    = p.get("ttlNm","")
        date     = parse_date_from_title(title, p.get("regDt",[]))
        is_new   = post_id not in existing_map
        cached   = existing_map.get(post_id,{})
        body     = cached.get("body","")
        summary  = cached.get("summary","")

        if (is_new or not body or "<p " in body or "<span" in body) and not summary:
            print(f"\n  글: {title[:50]}")
            body = fetch_detail_body(post_id, pst_type)
            if gemini_key and body:
                print(f"    → Gemini 요약 생성 중...")
                summary = summarize_with_gemini(title, body, "mofa", date, gemini_key)
            time.sleep(60)

        mofa_results.append({
            "id": post_id, "title": title,
            "type": pst_type, "type_name": p.get("pstTypeNm",""),
            "date": date, "url": get_detail_url(p),
            "body": body, "summary": summary, "is_new": is_new,
        })

    # 2. GC 업데이트
    print("\n=== GC 업데이트 (PDF) ===")
    gc_results = load_gc_posts(gemini_key, existing_map)

    # 3. NO 업데이트
    print("\n=== NO 업데이트 (TXT/DOCX) ===")
    no_results = load_no_posts(gemini_key, existing_map)

    # 수동으로 추가된 글 유지 (API에서 못 잡은 글)
    api_ids = {p["id"] for p in mofa_results}
    manual_posts = [p for p in existing.get("posts", []) 
                    if p["id"] not in api_ids 
                    and not p["id"].startswith("gc_") 
                    and not p["id"].startswith("no_")]
    
    # 날짜순 정렬 (최신순)
    api_ids = {p["id"] for p in mofa_results}
    manual_posts = [p for p in existing.get("posts", []) 
                    if p["id"] not in api_ids 
                    and not p["id"].startswith("gc_") 
                    and not p["id"].startswith("no_")]
    all_results = mofa_results + manual_posts + gc_results + no_results
    all_results.sort(key=lambda x: x.get("date",""), reverse=True)

    now_kst = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    output  = {"updated_at": now_kst, "total": len(all_results), "posts": all_results}
    os.makedirs("data", exist_ok=True)
    with open(OUTPUT_FILE,"w",encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n완료: {len(all_results)}건 / {now_kst}")

if __name__ == "__main__":
    run()

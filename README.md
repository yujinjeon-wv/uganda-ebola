# 🇺🇬 우간다 에볼라 안전공지 모니터

외교부 해외안전여행 사이트의 우간다 관련 안전공지를 자동으로 수집하여 가독성 좋게 보여주는 웹페이지입니다.

## 기능

- 외교부 API에서 우간다 관련 공지를 하루 3회 자동 수집
- Gemini AI가 각 공지를 2~3문장으로 자동 요약
- GitHub Pages를 통해 URL로 공유 가능

## 구조

```
├── index.html                      ← 웹페이지
├── data/posts.json                 ← 크롤링된 데이터 (자동 업데이트)
├── crawler/crawl.py                ← 크롤러 스크립트
└── .github/workflows/crawl.yml    ← 자동 실행 스케줄러 (하루 3회)
```

## 세팅 방법

### 1. GitHub Pages 활성화
Settings → Pages → Source: `main` 브랜치 `/` (root) → Save

### 2. Gemini API 키 등록 (AI 요약 기능)
Settings → Secrets and variables → Actions → New repository secret
- Name: `GEMINI_API_KEY`
- Value: Gemini API 키 ([aistudio.google.com](https://aistudio.google.com)에서 무료 발급)

### 3. 첫 실행
Actions 탭 → "우간다 안전공지 크롤러" → "Run workflow"

## 데이터 출처

[외교부 해외안전여행](https://www.0404.go.kr/bbs/safetyNtc/list)

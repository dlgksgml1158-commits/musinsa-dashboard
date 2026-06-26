# 무신사 × 29CM 랭킹 대시보드

> 무신사·29CM 실시간 순위 트래킹 대시보드 — MD 포트폴리오 & 팀 공유용

## 기능

- **플랫폼 탭** : 무신사 / 29CM 전환
- **카테고리 필터** : 상의, 아우터, 바지, 신발 등 카테고리별 랭킹
- **순위 변동 표시** : ▲ 상승 / ▼ 하락 / NEW 신규 진입
- **브랜드 노출 현황** : TOP 50 내 브랜드별 등장 횟수
- **카테고리 인기도** : 카테고리별 분포 막대 그래프
- **자동 데이터 갱신** : GitHub Actions가 매 1시간마다 랭킹 수집 & 업데이트

---

## GitHub Pages 배포 방법 (5분)

### 1. GitHub 레포지토리 생성

1. [github.com](https://github.com) 접속 → **New repository**
2. Repository name: `musinsa-dashboard` (원하는 이름)
3. **Public** 선택 → **Create repository**

### 2. 파일 업로드

```bash
# 터미널에서
git clone https://github.com/YOUR_ID/musinsa-dashboard.git
cd musinsa-dashboard

# 이 폴더의 모든 파일을 복사한 뒤
git add .
git commit -m "첫 배포"
git push origin main
```

### 3. GitHub Pages 활성화

1. 레포지토리 → **Settings** → **Pages**
2. Source: **Deploy from a branch**
3. Branch: **main** / **/ (root)** 선택 → **Save**
4. 약 1~2분 후 `https://YOUR_ID.github.io/musinsa-dashboard` 에 배포 완료

### 4. GitHub Actions 권한 설정

1. 레포지토리 → **Settings** → **Actions** → **General**
2. **Workflow permissions** → **Read and write permissions** 선택 → **Save**
3. **Actions** 탭 → `랭킹 데이터 자동 업데이트` → **Run workflow** 로 수동 테스트

---

## 노션에 임베드하기

1. 노션 페이지에서 `/embed` 입력
2. `https://YOUR_ID.github.io/musinsa-dashboard` 붙여넣기
3. **임베드** 버튼 클릭
4. 블록 크기 조절 (최소 800px 이상 권장)

---

## 파일 구조

```
musinsa-dashboard/
├── index.html                      # 메인 대시보드 (단일 파일)
├── data/
│   ├── musinsa.json                # 무신사 랭킹 데이터 (자동 업데이트)
│   └── 29cm.json                   # 29CM 랭킹 데이터 (자동 업데이트)
├── scripts/
│   └── fetch_rankings.py           # 데이터 수집 스크립트
└── .github/
    └── workflows/
        └── update-rankings.yml     # GitHub Actions 자동화
```

---

## 데이터 구조 (JSON)

```json
{
  "platform": "musinsa",
  "updatedAt": "2026-06-26T09:00:00.000Z",
  "items": [
    {
      "rank": 1,
      "brand": "커버낫",
      "name": "커버낫 오버핏 코튼 셔츠",
      "price": 59000,
      "discountRate": 0,
      "finalPrice": 59000,
      "category": "001",
      "categoryLabel": "상의",
      "change": "NEW",
      "id": "ms-12345"
    }
  ]
}
```

`change` 필드: `"NEW"` | 양수(상승) | 음수(하락) | `0`(변동없음)

---

## 스크립트 로컬 실행

```bash
pip install requests beautifulsoup4 lxml
python scripts/fetch_rankings.py
```

> **참고**: 무신사·29CM의 HTML 구조 변경 시 `fetch_rankings.py`의 CSS 선택자를 수정해야 할 수 있습니다.

---

*Made with ❤️ — MD 포트폴리오 프로젝트*

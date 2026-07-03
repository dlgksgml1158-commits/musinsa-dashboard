#!/usr/bin/env python3
"""
네이버 패션 인기 검색어 수집 스크립트
GitHub Actions에서 매일 자동 실행됩니다.

- 네이버 데이터랩 쇼핑 인사이트 패션 카테고리 인기 검색어 수집
- 이전 데이터와 비교하여 순위 변동 계산
- data/naver_trends.json 으로 저장
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

CATEGORIES = [
    {"cid": "50000167", "name": "상의",   "color": "#4ECDC4"},
    {"cid": "50000168", "name": "바지",   "color": "#45B7D1"},
    {"cid": "50000006", "name": "아우터", "color": "#96CEB4"},
    {"cid": "50000190", "name": "신발",   "color": "#FFEAA7"},
    {"cid": "50000086", "name": "가방",   "color": "#DDA0DD"},
]


def load_existing() -> list:
    path = DATA_DIR / "naver_trends.json"
    if not path.exists():
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
            return data.get("musinsa", [])
    except Exception:
        return []


def fetch_keywords():
    headers = {
        "User-Agent": BROWSER_UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ko-KR,ko;q=0.9",
        "Referer": "https://datalab.naver.com/shoppingInsight/sCategory.naver",
        "Origin": "https://datalab.naver.com",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    url = "https://datalab.naver.com/shoppingInsight/getKeywordRank.naver"
    today = datetime.now().strftime("%Y%m%d")

    musinsa_kws = []
    seen = set()

    for cat in CATEGORIES:
        payload = (
            f"cid={cat['cid']}&timeUnit=date"
            f"&startDate={today}&endDate={today}"
            "&age=&sex=&device="
        )
        try:
            resp = requests.post(url, data=payload, headers=headers, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                raw = data.get("result", [])[:5]
                for item in raw:
                    kw = item.get("keyword", "").strip()
                    if not kw or kw in seen:
                        continue
                    seen.add(kw)
                    musinsa_kws.append({
                        "keyword": kw,
                        "cat": cat["name"],
                        "catColor": cat["color"],
                        "rank": item.get("rank", 0),
                        "change": None,
                    })
                print(f"    [{cat['name']}] {len(raw)}개 수집")
            else:
                print(f"    [{cat['name']}] HTTP {resp.status_code}")
        except Exception as e:
            print(f"    [{cat['name']}] 오류: {e}")
        time.sleep(0.5)

    musinsa_kws = musinsa_kws[:10]
    cm29_kws = [dict(kw) for kw in musinsa_kws]

    for i, kw in enumerate(musinsa_kws):
        kw["rank"] = i + 1
    for i, kw in enumerate(cm29_kws):
        kw["rank"] = i + 1

    return musinsa_kws, cm29_kws


def compute_changes(current: list, old_data: list) -> list:
    old_map = {
        item["keyword"]: item["rank"]
        for item in old_data
        if "keyword" in item and "rank" in item
    }
    for item in current:
        kw = item.get("keyword", "")
        old_rank = old_map.get(kw)
        if old_rank is None:
            item["change"] = "NEW"
        else:
            diff = old_rank - item["rank"]
            item["change"] = diff if diff != 0 else None
    return current


def save(musinsa_kws: list, cm29_kws: list):
    path = DATA_DIR / "naver_trends.json"
    data = {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "musinsa": musinsa_kws,
        "cm29": cm29_kws,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  ✅ 저장 완료: {path}")
    print(f"     무신사: {len(musinsa_kws)}개  29CM: {len(cm29_kws)}개")


def main():
    print(f"\n{'='*50}")
    print(f"  네이버 인기 검색어 수집 시작: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*50}\n")

    old_data = load_existing()
    print(f"  기존 데이터: {len(old_data)}개 항목")

    print("  네이버 데이터랩 수집 중...")
    musinsa_kws, cm29_kws = fetch_keywords()

    if musinsa_kws or cm29_kws:
        musinsa_kws = compute_changes(musinsa_kws, old_data)
        cm29_kws = compute_changes(cm29_kws, old_data)
        save(musinsa_kws, cm29_kws)
    else:
        print("  [WARN] 데이터 수집 실패 - 기존 파일 유지")

    print(f"\n{'='*50}")
    print(f"  ✅ 완료: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    main()

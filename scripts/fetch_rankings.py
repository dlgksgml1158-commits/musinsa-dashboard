"""
무신사 & 29CM 랭킹 데이터 수집 스크립트
GitHub Actions에서 1시간마다 자동 실행됩니다.

- 무신사: client.musinsa.com 공식 API (pans/ranking)
- 29CM  : display-bff-api.29cm.co.kr 공식 API (plp/best/items, POST)
- 이전 데이터와 비교하여 순위 변동 계산
- data/musinsa.json, data/29cm.json 으로 저장
"""

import json
import time
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

# ── 경로 설정 ──────────────────────────────────────
ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

LIMIT = 30  # 1~30위만 수집


# ═══════════════════════════════════════════════════
#  무신사 API
# ═══════════════════════════════════════════════════

def fetch_musinsa() -> list:
    """
    무신사 실시간 랭킹 API (남성, 전체 카테고리, 25-29세 기준)
    GET https://client.musinsa.com/api/home/web/v5/pans/ranking
    """
    print("▶ 무신사 랭킹 수집 시작...")
    url = (
        "https://client.musinsa.com/api/home/web/v5/pans/ranking"
        "?storeCode=musinsa&sectionId=200&gf=M&categoryCode=000&ageBand=AGE_BAND_25"
    )
    headers = {
        "User-Agent": BROWSER_UA,
        "Referer": "https://www.musinsa.com/",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ko-KR,ko;q=0.9",
        "Origin": "https://www.musinsa.com",
    }

    try:
        resp = requests.get(url, headers=headers, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  [ERROR] 무신사 API 요청 실패: {e}")
        return []

    modules = data.get("data", {}).get("modules", [])
    raw_items = []
    for mod in modules:
        if mod.get("type") == "MULTICOLUMN" and mod.get("items"):
            for item in mod["items"]:
                if item.get("type") == "PRODUCT_COLUMN":
                    raw_items.append(item)

    # rank 기준 정렬 후 상위 30개
    raw_items.sort(key=lambda x: x.get("image", {}).get("rank", 9999))
    raw_items = raw_items[:LIMIT]

    result = []
    for item in raw_items:
        info = item.get("info", {})
        product_id = item.get("id", "")
        rank = item.get("image", {}).get("rank", 0)
        final_price = info.get("finalPrice", 0)
        discount = info.get("discountRatio", 0)
        original_price = round(final_price / (1 - discount / 100)) if discount else final_price

        result.append({
            "rank": rank,
            "id": f"ms-{product_id}",
            "brand": info.get("brandName", ""),
            "name": info.get("productName", ""),
            "price": final_price,
            "originalPrice": original_price,
            "discountRate": discount,
            "finalPrice": final_price,
            "category": "",
            "categoryLabel": "전체",
            "imgUrl": item.get("image", {}).get("url", ""),
            "productUrl": f"https://www.musinsa.com/goods/{product_id}",
            "change": None,
        })

    print(f"  ✓ 무신사 {len(result)}개 상품 수집 완료")
    return result


# ═══════════════════════════════════════════════════
#  29CM API
# ═══════════════════════════════════════════════════

def fetch_29cm() -> list:
    """
    29CM 실시간 베스트 랭킹 API (남성, 전체 카테고리, 30대 기준)
    POST https://display-bff-api.29cm.co.kr/api/v1/plp/best/items
    """
    print("▶ 29CM 랭킹 수집 시작...")
    url = "https://display-bff-api.29cm.co.kr/api/v1/plp/best/items"
    headers = {
        "User-Agent": BROWSER_UA,
        "Referer": "https://www.29cm.co.kr/best-products",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Origin": "https://www.29cm.co.kr",
    }
    payload = {
        "pageRequest": {"page": 1, "size": LIMIT},
        "userSegment": {"gender": "M", "age": "THIRTIES"},
        "facets": {
            "periodFacetInput": {"type": "HOURLY", "order": "DESC"},
            "rankingFacetInput": {"type": "POPULARITY"},
        },
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  [ERROR] 29CM API 요청 실패: {e}")
        return []

    items_raw = data.get("data", {}).get("list", [])
    result = []
    for idx, item in enumerate(items_raw[:LIMIT]):
        info = item.get("itemInfo", {})
        item_id = str(item.get("itemId", idx + 1))
        display_price = info.get("displayPrice", 0)
        original_price = info.get("originalPrice", display_price)
        sale_rate = info.get("saleRate", 0)

        result.append({
            "rank": idx + 1,
            "id": f"cm-{item_id}",
            "brand": info.get("brandName", ""),
            "name": info.get("productName", ""),
            "price": display_price,
            "originalPrice": original_price,
            "discountRate": sale_rate,
            "finalPrice": display_price,
            "category": "",
            "categoryLabel": "전체",
            "imgUrl": info.get("thumbnailUrl", ""),
            "productUrl": item.get("itemUrl", {}).get("webLink", f"https://www.29cm.co.kr/product/catalog/{item_id}"),
            "change": None,
        })

    print(f"  ✓ 29CM {len(result)}개 상품 수집 완료")
    return result


# ═══════════════════════════════════════════════════
#  순위 변동 계산
# ═══════════════════════════════════════════════════

def compute_rank_changes(new_items: list, old_items: list) -> list:
    old_rank_map = {item["id"]: item["rank"] for item in old_items}
    for item in new_items:
        old_rank = old_rank_map.get(item["id"])
        if old_rank is None:
            item["change"] = "NEW"
        else:
            delta = old_rank - item["rank"]
            item["change"] = delta if delta != 0 else 0
    return new_items


# ═══════════════════════════════════════════════════
#  파일 저장 / 로드
# ═══════════════════════════════════════════════════

def load_existing(platform: str) -> list:
    path = DATA_DIR / f"{platform}.json"
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f).get("items", [])
        except Exception:
            pass
    return []


def save(platform: str, data: dict):
    path = DATA_DIR / f"{platform}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  💾 저장 완료: {path}")


# ═══════════════════════════════════════════════════
#  메인
# ═══════════════════════════════════════════════════

def main():
    print(f"\n{'='*50}")
    print(f"  랭킹 수집 시작: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*50}\n")

    # 무신사
    old_musinsa = load_existing("musinsa")
    musinsa_items = fetch_musinsa()
    if musinsa_items:
        musinsa_items = compute_rank_changes(musinsa_items, old_musinsa)
        save("musinsa", {
            "platform": "musinsa",
            "updatedAt": datetime.now(timezone.utc).isoformat(),
            "items": musinsa_items,
        })
    else:
        print("  [WARN] 무신사 데이터 없음 - 기존 파일 유지")

    time.sleep(2)

    # 29CM
    old_29cm = load_existing("29cm")
    cm29_items = fetch_29cm()
    if cm29_items:
        cm29_items = compute_rank_changes(cm29_items, old_29cm)
        save("29cm", {
            "platform": "29cm",
            "updatedAt": datetime.now(timezone.utc).isoformat(),
            "items": cm29_items,
        })
    else:
        print("  [WARN] 29CM 데이터 없음 - 기존 파일 유지")

    print(f"\n{'='*50}")
    print(f"  ✅ 완료: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    main()

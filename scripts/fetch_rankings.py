"""
무신사 & 29CM 랭킹 데이터 수집 스크립트
GitHub Actions에서 1시간마다 자동 실행됩니다.

- 무신사: client.musinsa.com 공식 API (pans/ranking) - 전체 + 카테고리별 30위
- 29CM  : display-bff-api.29cm.co.kr 공식 API (plp/best/items, POST) - 전체 30위
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

# ── 무신사 카테고리 목록 ──────────────────────────
MUSINSA_CATEGORIES = [
    {"code": "",    "api": "000", "label": "전체"},
    {"code": "001", "api": "001", "label": "상의"},
    {"code": "002", "api": "002", "label": "아우터"},
    {"code": "003", "api": "003", "label": "바지"},
    {"code": "004", "api": "004", "label": "원피스/스커트"},
    {"code": "005", "api": "005", "label": "스포츠"},
    {"code": "020", "api": "020", "label": "신발"},
    {"code": "022", "api": "022", "label": "가방"},
    {"code": "023", "api": "023", "label": "시계/쥬얼리"},
    {"code": "024", "api": "024", "label": "패션잡화"},
    {"code": "026", "api": "026", "label": "화장품/향수"},
]


# ═══════════════════════════════════════════════════
#  무신사 API
# ═══════════════════════════════════════════════════

def _parse_musinsa_response(data: dict, cat_code: str, cat_label: str) -> list:
    """무신사 API 응답 파싱 → 상품 리스트"""
    modules = data.get("data", {}).get("modules", [])
    raw_items = []
    for mod in modules:
        if mod.get("type") == "MULTICOLUMN" and mod.get("items"):
            for item in mod["items"]:
                if item.get("type") == "PRODUCT_COLUMN":
                    raw_items.append(item)

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
            "category": cat_code,
            "categoryLabel": cat_label,
            "imgUrl": item.get("image", {}).get("url", ""),
            "productUrl": (
                item.get("link", {}).get("url", "") or
                f"https://www.musinsa.com/products/{product_id}"
            ),
            "change": None,
        })
    return result


def fetch_musinsa_category(api_code: str, cat_code: str, cat_label: str) -> list:
    """무신사 단일 카테고리 랭킹 수집"""
    url = (
        f"https://client.musinsa.com/api/home/web/v5/pans/ranking"
        f"?storeCode=musinsa&sectionId=200&gf=M&categoryCode={api_code}&ageBand=AGE_BAND_25"
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
        return _parse_musinsa_response(resp.json(), cat_code, cat_label)
    except Exception as e:
        print(f"  [ERROR] 무신사 {cat_label} 수집 실패: {e}")
        return []


def fetch_musinsa() -> dict:
    """
    무신사 실시간 랭킹 - 전체 + 카테고리별 30위 수집
    반환: { "": [...30개], "001": [...30개], ... }
    """
    print("▶ 무신사 랭킹 수집 시작 (전체 + 카테고리별)...")
    categories = {}

    for cat in MUSINSA_CATEGORIES:
        label = cat["label"]
        print(f"  - {label} 수집 중...")
        items = fetch_musinsa_category(cat["api"], cat["code"], label)
        categories[cat["code"]] = items
        print(f"    ✓ {len(items)}개")
        time.sleep(1)  # API 과부하 방지

    total = sum(len(v) for v in categories.values())
    print(f"  ✓ 무신사 전체 {total}개 상품 수집 완료 ({len(categories)}개 카테고리)")
    return categories


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
            "productUrl": item.get("itemUrl", {}).get("webLink", f"https://product.29cm.co.kr/catalog/{item_id}"),
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


def compute_rank_changes_categories(new_cats: dict, old_cats: dict) -> dict:
    """카테고리 구조에서 순위 변동 계산"""
    for code, items in new_cats.items():
        old_items = old_cats.get(code, [])
        new_cats[code] = compute_rank_changes(items, old_items)
    return new_cats


# ═══════════════════════════════════════════════════
#  파일 저장 / 로드
# ═══════════════════════════════════════════════════

def load_existing_categories(platform: str) -> dict:
    """기존 저장된 카테고리별 데이터 로드"""
    path = DATA_DIR / f"{platform}.json"
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                saved = json.load(f)
                # 새 형식: categories 키
                if "categories" in saved:
                    return saved["categories"]
                # 구 형식: items 키 (전체만)
                if "items" in saved:
                    return {"": saved["items"]}
        except Exception:
            pass
    return {}


def load_existing(platform: str) -> list:
    """구 형식 호환용 - 전체 items만 반환"""
    cats = load_existing_categories(platform)
    return cats.get("", [])


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

    # ── 무신사 (카테고리별) ──
    old_musinsa_cats = load_existing_categories("musinsa")
    musinsa_cats = fetch_musinsa()
    if musinsa_cats:
        musinsa_cats = compute_rank_changes_categories(musinsa_cats, old_musinsa_cats)
        # items 필드도 전체 데이터로 채워 구버전 호환 유지
        save("musinsa", {
            "platform": "musinsa",
            "updatedAt": datetime.now(timezone.utc).isoformat(),
            "items": musinsa_cats.get("", []),
            "categories": musinsa_cats,
        })
    else:
        print("  [WARN] 무신사 데이터 없음 - 기존 파일 유지")

    time.sleep(2)

    # ── 29CM (전체만) ──
    old_29cm = load_existing("29cm")
    cm29_items = fetch_29cm()
    if cm29_items:
        cm29_items = compute_rank_changes(cm29_items, old_29cm)
        save("29cm", {
            "platform": "29cm",
            "updatedAt": datetime.now(timezone.utc).isoformat(),
            "items": cm29_items,
            "categories": {"": cm29_items},
        })
    else:
        print("  [WARN] 29CM 데이터 없음 - 기존 파일 유지")

    print(f"\n{'='*50}")
    print(f"  ✅ 완료: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    main()

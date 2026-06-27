"""
무신사 & 29CM 랭킹 데이터 수집 스크립트
GitHub Actions에서 1시간마다 자동 실행됩니다.

- 무신사: api.musinsa.com/api2/json/rank/goods (카테고리별 전용 랭킹 API)
          실패 시 ranking 페이지 __NEXT_DATA__ HTML 파싱으로 폴백
- 29CM  : display-bff-api.29cm.co.kr (전체 30위)
- 이전 데이터와 비교하여 순위 변동 계산
- data/musinsa.json, data/29cm.json 으로 저장
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

LIMIT = 30

MUSINSA_CATEGORIES = [
    {"code": "",    "label": "전체"},
    {"code": "001", "label": "상의"},
    {"code": "002", "label": "아우터"},
    {"code": "003", "label": "바지"},
    {"code": "004", "label": "원피스/스커트"},
    {"code": "005", "label": "스포츠"},
    {"code": "020", "label": "신발"},
    {"code": "022", "label": "가방"},
    {"code": "023", "label": "시계/쥬얼리"},
    {"code": "024", "label": "패션잡화"},
    {"code": "026", "label": "화장품/향수"},
]


# ═══════════════════════════════════════════════════
#  공통 유틸
# ═══════════════════════════════════════════════════

PRODUCT_KEYS = {"goodsNo", "goodsId", "goodsName", "brandName", "salePrice", "normalPrice"}

def _search_product_list(data, depth=0) -> list:
    """JSON 트리에서 상품 배열 재귀 탐색"""
    if depth > 12:
        return []
    if isinstance(data, list) and len(data) >= 3:
        if data and isinstance(data[0], dict) and (set(data[0].keys()) & PRODUCT_KEYS):
            return data
    if isinstance(data, dict):
        for v in data.values():
            r = _search_product_list(v, depth + 1)
            if r:
                return r
    elif isinstance(data, list):
        for item in data:
            r = _search_product_list(item, depth + 1)
            if r:
                return r
    return []


def _build_item(raw: dict, rank: int, cat_code: str, cat_label: str) -> dict:
    """무신사 상품 raw dict → 표준 포맷 변환 (다양한 키 이름 대응)"""
    good_no = str(
        raw.get("goodsNo") or raw.get("goodsId") or raw.get("id") or ""
    )
    brand   = raw.get("brandName") or raw.get("brand") or ""
    name    = raw.get("goodsName") or raw.get("productName") or raw.get("name") or ""
    normal  = int(raw.get("normalPrice") or raw.get("originalPrice") or raw.get("price") or 0)
    sale    = int(raw.get("salePrice") or raw.get("finalPrice") or normal)
    disc    = int(raw.get("discountRate") or raw.get("discountRatio") or 0)
    img     = raw.get("goodsImgUrl") or raw.get("imageUrl") or raw.get("imgUrl") or ""

    return {
        "rank": rank,
        "id": f"ms-{good_no}",
        "brand": brand,
        "name": name,
        "price": normal,
        "originalPrice": normal,
        "discountRate": disc,
        "finalPrice": sale,
        "category": cat_code,
        "categoryLabel": cat_label,
        "imgUrl": img,
        "productUrl": f"https://www.musinsa.com/products/{good_no}" if good_no else "",
        "change": None,
    }


# ═══════════════════════════════════════════════════
#  무신사 — 방법 1: 랭킹 전용 API
# ═══════════════════════════════════════════════════

def _fetch_via_ranking_api(cat_code: str, cat_label: str) -> list:
    """api.musinsa.com/api2/json/rank/goods — 카테고리별 실시간 랭킹"""
    url = "https://api.musinsa.com/api2/json/rank/goods"
    params = {
        "period": "REALTIME",
        "categoryCode": cat_code,
        "display_cnt": LIMIT,
        "list_kind": "big",
        "page": 1,
        "gf": "A",
    }
    headers = {
        "User-Agent": BROWSER_UA,
        "Referer": "https://www.musinsa.com/ranking/best",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ko-KR,ko;q=0.9",
        "Origin": "https://www.musinsa.com",
    }
    resp = requests.get(url, params=params, headers=headers, timeout=20)
    resp.raise_for_status()
    data = resp.json()

    goods_list = (
        data.get("data", {}).get("list")
        or data.get("data", {}).get("goodsList")
        or data.get("list")
        or _search_product_list(data)
    )
    if not goods_list:
        return []

    result = []
    for idx, item in enumerate(goods_list[:LIMIT]):
        parsed = _build_item(item, idx + 1, cat_code, cat_label)
        if parsed["name"]:
            result.append(parsed)
    return result


# ═══════════════════════════════════════════════════
#  무신사 — 방법 2: 랭킹 페이지 HTML → __NEXT_DATA__
# ═══════════════════════════════════════════════════

def _fetch_via_html(cat_code: str, cat_label: str) -> list:
    """ranking/best HTML 페이지의 __NEXT_DATA__ JSON 파싱"""
    url = "https://www.musinsa.com/ranking/best"
    params = {
        "categoryCode": cat_code,
        "period": "now",
        "gf": "A",
        "display_cnt": LIMIT,
    }
    headers = {
        "User-Agent": BROWSER_UA,
        "Referer": "https://www.musinsa.com/",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9",
    }
    resp = requests.get(url, params=params, headers=headers, timeout=30)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    script = soup.find("script", {"id": "__NEXT_DATA__"})
    if not script:
        print(f"    [DEBUG] __NEXT_DATA__ 태그 없음 (카테고리={cat_code})")
        return []

    page_data = json.loads(script.string)
    goods_list = _search_product_list(page_data)

    if not goods_list:
        # 디버그: 상위 키 구조 출력
        def _print_keys(d, depth=0):
            if depth > 3 or not isinstance(d, dict):
                return
            for k, v in list(d.items())[:20]:
                tag = f"[{len(v)}]" if isinstance(v, (list, dict)) else ""
                print(f"    {'  '*depth}{k}: {type(v).__name__}{tag}")
                _print_keys(v, depth + 1)
        print(f"    [DEBUG] __NEXT_DATA__ 구조 (카테고리={cat_code}):")
        _print_keys(page_data)
        return []

    result = []
    for idx, item in enumerate(goods_list[:LIMIT]):
        parsed = _build_item(item, idx + 1, cat_code, cat_label)
        if parsed["name"]:
            result.append(parsed)
    return result


# ═══════════════════════════════════════════════════
#  무신사 — 방법 3: 구 홈 위젯 API (전체 폴백용)
# ═══════════════════════════════════════════════════

def _fetch_via_home_widget(cat_label: str) -> list:
    """전체 랭킹용 기존 홈 위젯 API (카테고리 필터 미지원)"""
    url = (
        "https://client.musinsa.com/api/home/web/v5/pans/ranking"
        "?storeCode=musinsa&sectionId=200&gf=M&categoryCode=000&ageBand=AGE_BAND_25"
    )
    headers = {
        "User-Agent": BROWSER_UA,
        "Referer": "https://www.musinsa.com/",
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://www.musinsa.com",
    }
    resp = requests.get(url, headers=headers, timeout=20)
    resp.raise_for_status()
    data = resp.json()

    modules = data.get("data", {}).get("modules", [])
    raw_items = []
    for mod in modules:
        if mod.get("type") == "MULTICOLUMN" and mod.get("items"):
            for item in mod["items"]:
                if item.get("type") == "PRODUCT_COLUMN":
                    raw_items.append(item)

    raw_items.sort(key=lambda x: x.get("image", {}).get("rank", 9999))

    result = []
    for item in raw_items[:LIMIT]:
        info = item.get("info", {})
        product_id = str(item.get("id", ""))
        final_price = info.get("finalPrice", 0)
        discount = info.get("discountRatio", 0)
        original_price = round(final_price / (1 - discount / 100)) if discount else final_price

        result.append({
            "rank": item.get("image", {}).get("rank", 0),
            "id": f"ms-{product_id}",
            "brand": info.get("brandName", ""),
            "name": info.get("productName", ""),
            "price": original_price,
            "originalPrice": original_price,
            "discountRate": discount,
            "finalPrice": final_price,
            "category": "",
            "categoryLabel": "전체",
            "imgUrl": item.get("image", {}).get("url", ""),
            "productUrl": (
                item.get("link", {}).get("url", "")
                or f"https://www.musinsa.com/products/{product_id}"
            ),
            "change": None,
        })
    return result


# ═══════════════════════════════════════════════════
#  무신사 메인
# ═══════════════════════════════════════════════════

def fetch_musinsa_category(cat_code: str, cat_label: str) -> list:
    """카테고리 랭킹 수집: 랭킹 API → HTML → 홈 위젯(전체만) 순으로 시도"""

    # 방법 1: 랭킹 전용 API
    try:
        result = _fetch_via_ranking_api(cat_code, cat_label)
        if result:
            print(f"    ✓ [랭킹API] {len(result)}개")
            return result
        print(f"    [WARN] 랭킹API 빈 응답")
    except Exception as e:
        print(f"    [WARN] 랭킹API 실패: {e}")

    # 방법 2: HTML __NEXT_DATA__
    try:
        result = _fetch_via_html(cat_code, cat_label)
        if result:
            print(f"    ✓ [HTML] {len(result)}개")
            return result
        print(f"    [WARN] HTML 파싱 결과 없음")
    except Exception as e:
        print(f"    [WARN] HTML 실패: {e}")

    # 방법 3: 전체 카테고리에만 홈 위젯 API 사용
    if not cat_code:
        try:
            result = _fetch_via_home_widget(cat_label)
            if result:
                print(f"    ✓ [홈위젯] {len(result)}개")
                return result
        except Exception as e:
            print(f"    [WARN] 홈위젯 실패: {e}")

    print(f"    [ERROR] 모든 방법 실패")
    return []


def fetch_musinsa() -> dict:
    print("▶ 무신사 랭킹 수집 시작...")
    categories = {}
    for cat in MUSINSA_CATEGORIES:
        print(f"  - {cat['label']} 수집 중...")
        items = fetch_musinsa_category(cat["code"], cat["label"])
        categories[cat["code"]] = items
        time.sleep(1.5)

    total = sum(len(v) for v in categories.values())
    print(f"  ✓ 무신사 {total}개 수집 완료 ({len(categories)}개 카테고리)")
    return categories


# ═══════════════════════════════════════════════════
#  29CM API
# ═══════════════════════════════════════════════════

def fetch_29cm() -> list:
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
        print(f"  [ERROR] 29CM 실패: {e}")
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

    print(f"  ✓ 29CM {len(result)}개 수집 완료")
    return result


# ═══════════════════════════════════════════════════
#  순위 변동 계산
# ═══════════════════════════════════════════════════

def compute_rank_changes(new_items: list, old_items: list) -> list:
    old_map = {item["id"]: item["rank"] for item in old_items}
    for item in new_items:
        old_rank = old_map.get(item["id"])
        if old_rank is None:
            item["change"] = "NEW"
        else:
            delta = old_rank - item["rank"]
            item["change"] = delta if delta != 0 else 0
    return new_items


def compute_rank_changes_categories(new_cats: dict, old_cats: dict) -> dict:
    for code, items in new_cats.items():
        new_cats[code] = compute_rank_changes(items, old_cats.get(code, []))
    return new_cats


# ═══════════════════════════════════════════════════
#  파일 저장 / 로드
# ═══════════════════════════════════════════════════

def load_existing_categories(platform: str) -> dict:
    path = DATA_DIR / f"{platform}.json"
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                saved = json.load(f)
                if "categories" in saved:
                    return saved["categories"]
                if "items" in saved:
                    return {"": saved["items"]}
        except Exception:
            pass
    return {}


def load_existing(platform: str) -> list:
    return load_existing_categories(platform).get("", [])


def save(platform: str, data: dict):
    path = DATA_DIR / f"{platform}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  💾 저장: {path}")


# ═══════════════════════════════════════════════════
#  메인
# ═══════════════════════════════════════════════════

def main():
    print(f"\n{'='*50}")
    print(f"  랭킹 수집 시작: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*50}\n")

    old_musinsa = load_existing_categories("musinsa")
    musinsa_cats = fetch_musinsa()
    if musinsa_cats:
        musinsa_cats = compute_rank_changes_categories(musinsa_cats, old_musinsa)
        save("musinsa", {
            "platform": "musinsa",
            "updatedAt": datetime.now(timezone.utc).isoformat(),
            "items": musinsa_cats.get("", []),
            "categories": musinsa_cats,
        })
    else:
        print("  [WARN] 무신사 데이터 없음 - 기존 파일 유지")

    time.sleep(2)

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

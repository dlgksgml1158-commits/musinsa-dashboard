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

_MUSINSA_CAT_ENDPOINT = ""
_MUSINSA_SECTION_IDS: dict = {}  # sectionId -> first product name (probe 결과)

# 무신사 카테고리 코드 → sectionId 매핑 후보 (probe 결과에 따라 결정됨)
# 현재는 unknown이므로 probe 후 자동 매핑 시도
MUSINSA_CAT_TO_SECTION: dict = {}  # code -> sectionId (str)


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

def _extract_multicolumn_products(modules: list) -> list:
    """MULTICOLUMN/PRODUCT_COLUMN 구조에서 상품 목록 추출"""
    raw_items = []
    for mod in modules:
        if mod.get("type") == "MULTICOLUMN" and mod.get("items"):
            for item in mod["items"]:
                if item.get("type") == "PRODUCT_COLUMN":
                    raw_items.append(item)
    raw_items.sort(key=lambda x: x.get("image", {}).get("rank", 9999))
    return raw_items


def _multicolumn_to_items(raw_items: list, cat_code: str, cat_label: str) -> list:
    """PRODUCT_COLUMN 아이템 목록 → 표준 포맷 변환"""
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
            "category": cat_code,
            "categoryLabel": cat_label,
            "imgUrl": item.get("image", {}).get("url", ""),
            "productUrl": (
                item.get("link", {}).get("url", "")
                or f"https://www.musinsa.com/products/{product_id}"
            ),
            "change": None,
        })
    return result


def _fetch_all_musinsa_via_homepage() -> dict:
    """
    Playwright로 무신사 홈페이지를 열고 랭킹 섹션의 카테고리 탭을 클릭하며
    각 카테고리 API 응답을 인터셉트해서 반환.
    반환: {cat_code: [items]} 또는 빈 dict (실패 시)
    """
    if not _init_playwright():
        return {}

    # 카테고리 탭 레이블 (홈페이지 DOM에서 찾을 텍스트)
    cat_tab_map = [
        ("001", "상의"),
        ("002", "아우터"),
        ("003", "바지"),
        ("004", "원피스/스커트"),
        ("005", "스포츠"),
        ("020", "신발"),
        ("022", "가방"),
        ("023", "시계/쥬얼리"),
        ("024", "패션잡화"),
        ("026", "화장품/향수"),
    ]

    context = _PW_BROWSER.new_context(
        user_agent=BROWSER_UA,
        locale="ko-KR",
        extra_http_headers={"Accept-Language": "ko-KR,ko;q=0.9"},
    )
    page = context.new_page()
    last_captured = []  # 가장 최근 랭킹 API 응답

    def on_response(response):
        nonlocal last_captured
        if "client.musinsa.com/api" in response.url and response.status == 200:
            try:
                body = response.json()
                mods = body.get("data", {}).get("modules", [])
                items = _extract_multicolumn_products(mods)
                if items:
                    last_captured = items
            except Exception:
                pass

    page.on("response", on_response)
    results = {}

    try:
        page.goto("https://www.musinsa.com/", wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(2000)

        # 전체 (페이지 초기 로드 데이터)
        all_items = list(last_captured)
        if all_items:
            results[""] = all_items
            print(f"    [HOME] 전체: {len(all_items)}개 (초기 로드)")
        else:
            print("    [HOME] 전체 초기 로드 실패")

        # 랭킹 섹션 내 카테고리 탭 목록 로그 (디버그)
        try:
            tabs_text = page.evaluate("""
                () => {
                    const allButtons = [...document.querySelectorAll('button, [role="tab"], li')];
                    return allButtons
                        .filter(el => el.offsetParent !== null)
                        .map(el => el.textContent.trim())
                        .filter(t => t.length > 0 && t.length < 30)
                        .slice(0, 50);
                }
            """)
            print(f"    [HOME] 페이지 탭/버튼 목록: {tabs_text[:20]}")
        except Exception:
            pass

        # 각 카테고리 탭 클릭
        for cat_code, cat_label in cat_tab_map:
            last_captured = []
            clicked = False
            selectors = [
                f'button:text-is("{cat_label}")',
                f'[role="tab"]:text-is("{cat_label}")',
                f'li:text-is("{cat_label}")',
                f'span:text-is("{cat_label}")',
                f'a:text-is("{cat_label}")',
                f'button:has-text("{cat_label}")',
            ]
            for sel in selectors:
                try:
                    el = page.locator(sel).first
                    if el.is_visible(timeout=1500):
                        el.click()
                        page.wait_for_timeout(2000)
                        clicked = True
                        break
                except Exception:
                    continue

            if clicked and last_captured:
                results[cat_code] = list(last_captured)
                print(f"    [HOME] {cat_label}: {len(last_captured)}개 (탭 클릭)")
            elif clicked:
                print(f"    [HOME] {cat_label}: 탭 클릭했으나 API 응답 없음 → 전체 데이터 사용")
            else:
                print(f"    [HOME] {cat_label}: 탭 미발견")

    except Exception as e:
        print(f"    [HOME] 홈페이지 오류: {e}")
    finally:
        context.close()

    return results


# ═══════════════════════════════════════════════════
#  무신사 — Playwright 공유 브라우저 인스턴스
# ═══════════════════════════════════════════════════

_PW_BROWSER = None
_PW_INSTANCE = None

def _init_playwright():
    global _PW_BROWSER, _PW_INSTANCE
    if _PW_BROWSER is not None:
        return True
    try:
        from playwright.sync_api import sync_playwright
        _PW_INSTANCE = sync_playwright().start()
        _PW_BROWSER = _PW_INSTANCE.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
        )
        print("  [PW] 브라우저 초기화 완료")
        return True
    except Exception as e:
        print(f"  [PW] 브라우저 초기화 실패: {e}")
        return False

def _close_playwright():
    global _PW_BROWSER, _PW_INSTANCE
    try:
        if _PW_BROWSER:
            _PW_BROWSER.close()
        if _PW_INSTANCE:
            _PW_INSTANCE.stop()
    except Exception:
        pass
    _PW_BROWSER = None
    _PW_INSTANCE = None


# ═══════════════════════════════════════════════════
#  무신사 — 홈 위젯 API (전체 폴백)
# ═══════════════════════════════════════════════════

def _fetch_via_home_widget_all() -> list:
    """sectionId=200 홈 위젯으로 전체 랭킹 30개 수집"""
    url = "https://client.musinsa.com/api/home/web/v5/pans/ranking"
    params = {
        "storeCode": "musinsa",
        "sectionId": "200",
        "gf": "M",
        "categoryCode": "000",
        "ageBand": "AGE_BAND_25",
    }
    headers = {
        "User-Agent": BROWSER_UA,
        "Referer": "https://www.musinsa.com/",
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://www.musinsa.com",
    }
    resp = requests.get(url, params=params, headers=headers, timeout=20)
    resp.raise_for_status()
    mods = resp.json().get("data", {}).get("modules", [])
    return _extract_multicolumn_products(mods)


# ═══════════════════════════════════════════════════
#  무신사 메인
# ═══════════════════════════════════════════════════

def fetch_musinsa() -> dict:
    print("▶ 무신사 랭킹 수집 시작...")

    # 방법 1: Playwright 홈페이지 + 카테고리 탭 클릭으로 모든 카테고리 일괄 수집
    homepage_results = {}
    try:
        homepage_results = _fetch_all_musinsa_via_homepage()
        print(f"  [HOME] 홈페이지에서 {len(homepage_results)}개 카테고리 수집")
    except Exception as e:
        print(f"  [WARN] 홈페이지 수집 실패: {e}")

    # 방법 2: 홈 위젯 API로 전체 랭킹 수집 (homepage_results에 없는 경우)
    fallback_all = []
    try:
        fallback_all = _fetch_via_home_widget_all()
        print(f"  [홈위젯] 전체 랭킹: {len(fallback_all)}개")
    except Exception as e:
        print(f"  [WARN] 홈 위젯 실패: {e}")

    categories = {}
    for cat in MUSINSA_CATEGORIES:
        cat_code = cat["code"]
        cat_label = cat["label"]
        print(f"  - {cat_label} 수집 중...")

        raw_items = homepage_results.get(cat_code)
        if raw_items:
            items = _multicolumn_to_items(raw_items, cat_code, cat_label)
            if items:
                categories[cat_code] = items
                print(f"    ✓ [홈-탭] {len(items)}개")
                continue

        # 전체 카테고리 폴백: 홈 위젯 데이터 사용
        if not cat_code and fallback_all:
            categories[cat_code] = _multicolumn_to_items(fallback_all, cat_code, cat_label)
            print(f"    ✓ [홈위젯-폴백] {len(categories[cat_code])}개")
        else:
            # 카테고리 탭도 실패 → 전체 데이터로 채움 (임시)
            if fallback_all:
                categories[cat_code] = _multicolumn_to_items(fallback_all, cat_code, cat_label)
                print(f"    [임시] {cat_label}: 전체 데이터 사용 ({len(categories[cat_code])}개)")
            else:
                categories[cat_code] = []
                print(f"    [ERROR] {cat_label}: 수집 실패")

    total = sum(len(v) for v in categories.values())
    print(f"  ✓ 무신사 {total}개 수집 완료 ({len(categories)}개 카테고리)")
    return categories


# ═══════════════════════════════════════════════════
#  29CM API
# ═══════════════════════════════════════════════════

CM29_CATEGORIES = [
    {"code": "",        "label": "전체",        "param": None},
    {"code": "tops",    "label": "상의",        "param": {"front_category_id": 268}},
    {"code": "outer",   "label": "아우터",      "param": {"front_category_id": 269}},
    {"code": "bottoms", "label": "하의",        "param": {"front_category_id": 270}},
    {"code": "dress",   "label": "원피스",      "param": {"front_category_id": 271}},
    {"code": "shoes",   "label": "신발",        "param": {"front_category_id": 278}},
    {"code": "bag",     "label": "가방",        "param": {"front_category_id": 280}},
    {"code": "acc",     "label": "액세서리",    "param": {"front_category_id": 282}},
    {"code": "beauty",  "label": "뷰티",        "param": {"front_category_id": 283}},
    {"code": "life",    "label": "라이프",      "param": {"front_category_id": 284}},
]

_CM29_CAT_PARAM_KEY = ""  # 'front_category_id' or other key discovered by probe


def _probe_29cm_category_endpoint() -> str:
    """29CM 카테고리별 베스트 API 파라미터 탐색. 상의 카테고리로 테스트."""
    url = "https://display-bff-api.29cm.co.kr/api/v1/plp/best/items"
    headers = {
        "User-Agent": BROWSER_UA,
        "Referer": "https://www.29cm.co.kr/best-products",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Origin": "https://www.29cm.co.kr",
    }

    # 전체 1위 기준
    ref_name = ""
    try:
        base_payload = {
            "pageRequest": {"page": 1, "size": LIMIT},
            "userSegment": {"gender": "M", "age": "THIRTIES"},
            "facets": {
                "periodFacetInput": {"type": "HOURLY", "order": "DESC"},
                "rankingFacetInput": {"type": "POPULARITY"},
            },
        }
        r = requests.post(url, json=base_payload, headers=headers, timeout=15)
        items = r.json().get("data", {}).get("list", [])
        if items:
            ref_name = items[0].get("itemInfo", {}).get("productName", "")
    except Exception:
        pass
    print(f"  [PROBE-29CM] 전체 기준 1위: {ref_name[:30]}")

    # BFF API — 본문 파라미터 변형
    post_candidates = [
        ("facet-category-268",  {"categoryFacetInput": {"categoryId": 268}}),
        ("facet-category-1",    {"categoryFacetInput": {"categoryId": 1}}),
        ("front_category_id=268", {"front_category_id": 268}),
        ("category_id=268",     {"category_id": 268}),
        ("itemClassCode=268",   {"itemClassCode": 268}),
    ]

    for label, extra_params in post_candidates:
        try:
            payload = {
                "pageRequest": {"page": 1, "size": LIMIT},
                "userSegment": {"gender": "M", "age": "THIRTIES"},
                "facets": {
                    "periodFacetInput": {"type": "HOURLY", "order": "DESC"},
                    "rankingFacetInput": {"type": "POPULARITY"},
                    **extra_params,
                } if label.startswith("facet") else {
                    "periodFacetInput": {"type": "HOURLY", "order": "DESC"},
                    "rankingFacetInput": {"type": "POPULARITY"},
                },
            }
            if not label.startswith("facet"):
                payload.update(extra_params)
            r = requests.post(url, json=payload, headers=headers, timeout=10)
            if not r.ok:
                print(f"  [PROBE-29CM] {label}: HTTP {r.status_code}")
                continue
            items = r.json().get("data", {}).get("list", [])
            first = items[0].get("itemInfo", {}).get("productName", "") if items else ""
            if first and first != ref_name:
                print(f"  [PROBE-29CM] ✓ 카테고리 데이터! {label}: 1위={first[:30]}")
                return label
            elif first:
                print(f"  [PROBE-29CM] {label}: 응답OK BUT 전체와 동일 1위={first[:30]}")
            else:
                print(f"  [PROBE-29CM] {label}: 응답OK 상품없음 keys={list(r.json().keys())[:5]}")
        except Exception as e:
            print(f"  [PROBE-29CM] {label}: 오류={e}")

    # 다른 엔드포인트 시도 (GET)
    get_candidates = [
        ("GET-best-category", "https://display-bff-api.29cm.co.kr/api/v1/plp/best/category/items",
         {"categoryId": 268, "size": 30}),
        ("GET-category-best", "https://display-bff-api.29cm.co.kr/api/v1/plp/category/best",
         {"categoryId": 268, "size": 30}),
        ("GET-category-rank", "https://api.29cm.co.kr/api/v1/products/ranking",
         {"categoryCode": "TOP", "size": 30}),
    ]
    for label, g_url, g_params in get_candidates:
        try:
            r = requests.get(g_url, params=g_params, headers=headers, timeout=8)
            print(f"  [PROBE-29CM] {label}: HTTP {r.status_code} url={g_url}")
            if r.ok:
                items = r.json().get("data", {}).get("list", []) if isinstance(r.json(), dict) else []
                first = items[0].get("itemInfo", {}).get("productName", "") if items else ""
                if first and first != ref_name:
                    print(f"  [PROBE-29CM] ✓ 카테고리 데이터! {label}: 1위={first[:30]}")
                    # GET 방식 엔드포인트는 별도 표시
                    return "GET|" + label + "|" + g_url + "|" + json.dumps(g_params)
        except Exception as e:
            print(f"  [PROBE-29CM] {label}: 오류={e}")

    print("  [PROBE-29CM] 카테고리 파라미터 미발견, 전체만 수집")
    return ""


def _fetch_29cm_items(extra_params: dict | None) -> list:
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
    if extra_params:
        payload.update(extra_params)

    resp = requests.post(url, json=payload, headers=headers, timeout=20)
    resp.raise_for_status()
    return resp.json().get("data", {}).get("list", [])


def _build_29cm_item(item: dict, rank: int, cat_code: str, cat_label: str) -> dict:
    info = item.get("itemInfo", {})
    item_id = str(item.get("itemId", ""))
    display_price = info.get("displayPrice", 0)
    original_price = info.get("originalPrice", display_price)
    sale_rate = info.get("saleRate", 0)
    return {
        "rank": rank,
        "id": f"cm-{item_id}",
        "brand": info.get("brandName", ""),
        "name": info.get("productName", ""),
        "price": display_price,
        "originalPrice": original_price,
        "discountRate": sale_rate,
        "finalPrice": display_price,
        "category": cat_code,
        "categoryLabel": cat_label,
        "imgUrl": info.get("thumbnailUrl", ""),
        "productUrl": item.get("itemUrl", {}).get("webLink", f"https://product.29cm.co.kr/catalog/{item_id}"),
        "change": None,
    }


def fetch_29cm() -> dict:
    global _CM29_CAT_PARAM_KEY
    print("▶ 29CM 랭킹 수집 시작...")
    print("  카테고리 API 탐색 중...")
    _CM29_CAT_PARAM_KEY = _probe_29cm_category_endpoint()

    categories = {}
    for cat in CM29_CATEGORIES:
        code = cat["code"]
        label = cat["label"]
        param = cat["param"]

        # 카테고리 API 미발견 시 전체만
        if code and not _CM29_CAT_PARAM_KEY:
            categories[code] = []
            continue

        try:
            raw_items = _fetch_29cm_items(param)
            result = [
                _build_29cm_item(item, idx + 1, code, label)
                for idx, item in enumerate(raw_items[:LIMIT])
            ]
            categories[code] = result
            print(f"    ✓ [{label}] {len(result)}개")
        except Exception as e:
            print(f"    [WARN] 29CM [{label}] 실패: {e}")
            categories[code] = []
        time.sleep(1)

    total = sum(len(v) for v in categories.values())
    print(f"  ✓ 29CM {total}개 수집 완료 ({len(categories)}개 카테고리)")
    return categories


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

    old_29cm_cats = load_existing_categories("29cm")
    cm29_cats = fetch_29cm()
    if cm29_cats and any(cm29_cats.values()):
        cm29_cats = compute_rank_changes_categories(cm29_cats, old_29cm_cats)
        save("29cm", {
            "platform": "29cm",
            "updatedAt": datetime.now(timezone.utc).isoformat(),
            "items": cm29_cats.get("", []),
            "categories": cm29_cats,
        })
    else:
        print("  [WARN] 29CM 데이터 없음 - 기존 파일 유지")

    print(f"\n{'='*50}")
    print(f"  ✅ 완료: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    main()

"""
무신사 & 29CM 랭킹 데이터 수집 스크립트
GitHub Actions에서 1시간마다 자동 실행됩니다.

- 무신사: api.musinsa.com/api2/json/rank/goods (카테고리별 전용 랭킹 API 1순위)
          client.musinsa.com home widget (전체 폴백)
          Playwright 홈페이지 탭 클릭 (최후 수단)
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

def _build_item(raw: dict, rank: int, cat_code: str, cat_label: str) -> dict:
    """무신사 상품 raw dict → 표준 포맷 변환"""
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
#  무신사 — 방법 1: 카테고리별 랭킹 전용 API
# ═══════════════════════════════════════════════════

_MUSINSA_API_WORKING = None  # None=미확인, True=동작, False=불가

def _fetch_musinsa_category_api(cat_code: str) -> list:
    """
    api.musinsa.com/api2/json/rank/goods 로 카테고리별 랭킹 수집.
    cat_code="" → 전체, "001" → 상의 등
    """
    url = "https://api.musinsa.com/api2/json/rank/goods"
    params: dict = {"page": 1, "size": LIMIT, "storeCode": "musinsa"}
    if cat_code:
        params["contentsCode"] = cat_code

    headers = {
        "User-Agent": BROWSER_UA,
        "Referer": "https://www.musinsa.com/ranking/best",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ko-KR,ko;q=0.9",
        "Origin": "https://www.musinsa.com",
    }
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        if resp.status_code != 200:
            return []
        data = resp.json()
        # 응답 형식 탐색: data.list 또는 data.goods 또는 list 최상위
        candidates = [
            data.get("data", {}).get("list", []),
            data.get("data", {}).get("goods", []),
            data.get("list", []),
            data.get("goods", []),
        ]
        for candidate in candidates:
            if candidate and isinstance(candidate, list) and isinstance(candidate[0], dict):
                return candidate
        return []
    except Exception:
        return []


def _probe_musinsa_api() -> bool:
    """api.musinsa.com 엔드포인트가 동작하는지 확인하고 전체와 상의 결과가 다른지 검증"""
    global _MUSINSA_API_WORKING
    if _MUSINSA_API_WORKING is not None:
        return _MUSINSA_API_WORKING

    all_items = _fetch_musinsa_category_api("")
    top_items = _fetch_musinsa_category_api("001")

    if not all_items or not top_items:
        print(f"  [API-MS] 카테고리 API 응답 없음 (전체:{len(all_items)}, 상의:{len(top_items)})")
        _MUSINSA_API_WORKING = False
        return False

    # 1위 상품명 비교
    def _first_name(items):
        r = items[0]
        return r.get("goodsName") or r.get("productName") or r.get("name") or ""

    all_first = _first_name(all_items)
    top_first = _first_name(top_items)
    print(f"  [API-MS] 전체 1위: {all_first[:30]}")
    print(f"  [API-MS] 상의 1위: {top_first[:30]}")

    if all_first and top_first and all_first != top_first:
        print("  [API-MS] ✓ 카테고리별 다른 결과 확인 → API 사용")
        _MUSINSA_API_WORKING = True
    elif all_first:
        print("  [API-MS] 전체와 상의 결과 동일 → API 무효 (categoryCode 미지원)")
        _MUSINSA_API_WORKING = False
    else:
        _MUSINSA_API_WORKING = False

    return _MUSINSA_API_WORKING


# ═══════════════════════════════════════════════════
#  무신사 — 방법 2: 홈 위젯 API (전체 전용)
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


def _fetch_via_home_widget_all() -> list:
    """sectionId=200 홈 위젯으로 전체 랭킹 수집"""
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
#  무신사 — 방법 3: Playwright (최후 수단)
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


def _fetch_all_musinsa_via_playwright() -> dict:
    """
    Playwright로 무신사 한국 홈페이지에 접속하여 카테고리 탭을 클릭하며
    각 카테고리 API 응답을 인터셉트.
    반환: {cat_code: [PRODUCT_COLUMN items]}
    """
    if not _init_playwright():
        return {}

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
    last_captured = []

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
        # 1단계: 글로벌 국가 선택 페이지 → 한국 선택
        print("    [PW] 글로벌 국가 선택 페이지 접속...")
        page.goto("https://global.musinsa.com/choose-location", wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(2000)
        print(f"    [PW] 현재 URL: {page.url[:70]}")

        # Korea 링크/버튼 클릭
        korea_clicked = False
        for sel in [
            'a[href*="musinsa.com"]:has-text("Korea")',
            'a:has-text("Korea")',
            'button:has-text("Korea")',
            'a:has-text("한국")',
            '[data-country="KR"]',
            'a[href*="/kr"]',
        ]:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=2000):
                    href = el.get_attribute("href") or ""
                    print(f"    [PW] Korea 링크 발견: {sel} href={href[:50]}")
                    el.click()
                    page.wait_for_timeout(3000)
                    korea_clicked = True
                    print(f"    [PW] 클릭 후 URL: {page.url[:70]}")
                    break
            except Exception:
                continue

        if not korea_clicked:
            # DOM 내 링크 목록 출력 (디버그)
            try:
                links = page.evaluate("""
                    () => [...document.querySelectorAll('a[href]')]
                        .map(a => a.href + ' | ' + a.textContent.trim())
                        .filter(s => s.length < 100)
                        .slice(0, 20)
                """)
                print(f"    [PW] 페이지 링크 목록: {links}")
            except Exception:
                pass
            print("    [PW] Korea 버튼 미발견 — www.musinsa.com/store 직접 시도")

        # 2단계: 무신사 스토어 홈 접속 (랭킹 섹션 로드 대기)
        store_urls = [
            "https://www.musinsa.com/store",
            "https://www.musinsa.com/",
        ]
        for store_url in store_urls:
            last_captured = []
            page.goto(store_url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(5000)  # React hydration 대기
            print(f"    [PW] 스토어 접속: {store_url} → {page.url[:70]}")
            if last_captured:
                print(f"    [PW] 초기 로드 {len(last_captured)}개 상품 캡처")
                break

        # 전체 데이터 (초기 로드)
        all_items = list(last_captured)
        if all_items:
            results[""] = all_items

        # 탭 목록 확인 (디버그)
        try:
            tabs_text = page.evaluate("""
                () => {
                    const els = [...document.querySelectorAll('button, [role="tab"], li')];
                    return els
                        .filter(el => el.offsetParent !== null)
                        .map(el => el.textContent.trim())
                        .filter(t => t.length > 0 && t.length < 20)
                        .slice(0, 30);
                }
            """)
            print(f"    [PW] 페이지 탭/버튼: {tabs_text}")
        except Exception:
            pass

        # 3단계: 카테고리 탭 클릭
        for cat_code, cat_label in cat_tab_map:
            last_captured = []
            clicked = False
            for sel in [
                f'button:text-is("{cat_label}")',
                f'[role="tab"]:text-is("{cat_label}")',
                f'li:text-is("{cat_label}")',
                f'span:text-is("{cat_label}")',
                f'a:text-is("{cat_label}")',
                f'button:has-text("{cat_label}")',
            ]:
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
                print(f"    [PW] {cat_label}: {len(last_captured)}개 (탭 클릭)")
            elif clicked:
                print(f"    [PW] {cat_label}: 클릭했으나 API 응답 없음")
            else:
                print(f"    [PW] {cat_label}: 탭 미발견")

    except Exception as e:
        print(f"    [PW] 오류: {e}")
    finally:
        context.close()

    return results


# ═══════════════════════════════════════════════════
#  무신사 메인
# ═══════════════════════════════════════════════════

def fetch_musinsa() -> dict:
    print("▶ 무신사 랭킹 수집 시작...")

    # ── 방법 1: 카테고리별 전용 API ──────────────────
    api_works = _probe_musinsa_api()
    if api_works:
        categories = {}
        for cat in MUSINSA_CATEGORIES:
            cat_code = cat["code"]
            cat_label = cat["label"]
            print(f"  - {cat_label} 수집 중...")
            raw = _fetch_musinsa_category_api(cat_code)
            if raw:
                items = [_build_item(r, idx + 1, cat_code, cat_label) for idx, r in enumerate(raw[:LIMIT])]
                categories[cat_code] = items
                print(f"    ✓ [API] {len(items)}개")
            else:
                categories[cat_code] = []
                print(f"    [WARN] {cat_label}: API 응답 없음")
            time.sleep(0.5)
        total = sum(len(v) for v in categories.values())
        print(f"  ✓ 무신사 {total}개 수집 완료 (카테고리별 API)")
        return categories

    # ── 방법 2: Playwright 홈페이지 탭 클릭 ──────────
    print("  [WARN] 카테고리 API 무효 → Playwright 홈페이지 시도")
    playwright_results = {}
    try:
        playwright_results = _fetch_all_musinsa_via_playwright()
        print(f"  [PW] {len(playwright_results)}개 카테고리 수집")
    except Exception as e:
        print(f"  [WARN] Playwright 실패: {e}")

    # ── 방법 3: 홈 위젯 API (전체 폴백) ──────────────
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

        raw_items = playwright_results.get(cat_code)
        if raw_items:
            items = _multicolumn_to_items(raw_items, cat_code, cat_label)
            if items:
                categories[cat_code] = items
                print(f"    ✓ [PW-탭] {len(items)}개")
                continue

        if not cat_code and fallback_all:
            categories[cat_code] = _multicolumn_to_items(fallback_all, cat_code, cat_label)
            print(f"    ✓ [홈위젯] {len(categories[cat_code])}개")
        else:
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

_CM29_CAT_PARAM_KEY = ""


def _probe_29cm_category_endpoint() -> str:
    """29CM 카테고리별 베스트 API 파라미터 탐색"""
    url = "https://display-bff-api.29cm.co.kr/api/v1/plp/best/items"
    headers = {
        "User-Agent": BROWSER_UA,
        "Referer": "https://www.29cm.co.kr/best-products",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Origin": "https://www.29cm.co.kr",
    }

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
                print(f"  [PROBE-29CM] {label}: 상품없음")
        except Exception as e:
            print(f"  [PROBE-29CM] {label}: 오류={e}")

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

    _close_playwright()

    print(f"\n{'='*50}")
    print(f"  ✅ 완료: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    main()

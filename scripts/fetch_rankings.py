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
import re
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
    {"code": "",       "label": "전체"},
    # 상의 서브카테고리
    {"code": "001001", "label": "반소매 티셔츠"},
    {"code": "001002", "label": "긴소매 티셔츠"},
    {"code": "001003", "label": "맨투맨/스웨트"},
    {"code": "001004", "label": "후드 티셔츠"},
    {"code": "001005", "label": "셔츠/블라우스"},
    {"code": "001006", "label": "니트/스웨터"},
    # 아우터
    {"code": "002",    "label": "아우터"},
    # 바지 서브카테고리
    {"code": "003001", "label": "데님 팬츠"},
    {"code": "003002", "label": "슬랙스"},
    {"code": "003003", "label": "트레이닝/조거"},
    {"code": "003005", "label": "반바지"},
]

# 위 코드(내부 표시용, UI/가격통계 등 기존 코드와의 호환을 위해 유지)를
# 무신사 실제 카테고리 코드(api2/dp/v2/plp/goods 에서 사용되는 진짜 코드)로
# 매핑한다. 직접 웹사이트를 방문해 __NEXT_DATA__에서 확인한 실제 코드:
#   상의(001) 하위: 001001 반소매 티셔츠, 001002 셔츠/블라우스, 001003 피케/카라 티셔츠,
#                   001004 후드 티셔츠, 001005 맨투맨/스웨트, 001006 니트/스웨터,
#                   001008 기타 상의, 001010 긴소매 티셔츠, 001011 민소매 티셔츠
#   바지(003) 하위: 003002 데님 팬츠, 003004 트레이닝/조거 팬츠, 003005 레깅스,
#                   003006 기타 하의, 003007 코튼 팬츠, 003008 슈트 팬츠/슬랙스,
#                   003009 숏 팬츠, 003010 점프 슈트/오버올
# 우리 내부 코드(001002=긴소매티셔츠 등)는 실제 코드와 이름이 어긋나 있었으므로
# 아래 매핑으로 실제 코드에 연결한다.
MUSINSA_REAL_CATEGORY_CODE = {
    "001001": "001001",  # 반소매 티셔츠
    "001002": "001010",  # 긴소매 티셔츠
    "001003": "001005",  # 맨투맨/스웨트
    "001004": "001004",  # 후드 티셔츠
    "001005": "001002",  # 셔츠/블라우스
    "001006": "001006",  # 니트/스웨터
    "002":    "002",     # 아우터
    "003001": "003002",  # 데님 팬츠
    "003002": "003008",  # 슬랙스
    "003003": "003004",  # 트레이닝/조거
    "003005": "003009",  # 반바지
}


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
#  무신사 — 방법 0: 실제 카테고리 상품 목록(PLP) API
# ═══════════════════════════════════════════════════
# www.musinsa.com/category/<code>/goods?sortCode=POPULAR 페이지가 실제로
# 호출하는 API. 진짜 인기순 정렬 + 페이지네이션을 지원하며, 세부 서브카테고리
# 코드로도 정확히 필터링된다(직접 확인 완료). hmacId 파라미터가 응답에
# 포함되지만 요청 시 없어도 정상 동작한다.
def _fetch_musinsa_plp_goods(real_cat_code: str, size: int = 30) -> list:
    url = "https://api.musinsa.com/api2/dp/v2/plp/goods"
    params = {
        "gf": "A",
        "sortCode": "POPULAR",
        "category": real_cat_code,
        "size": size,
        "caller": "CATEGORY",
        "page": 1,
    }
    headers = {
        "User-Agent": BROWSER_UA,
        "Referer": f"https://www.musinsa.com/category/{real_cat_code}/goods?sortCode=POPULAR",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ko-KR,ko;q=0.9",
        "Origin": "https://www.musinsa.com",
    }
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        if resp.status_code != 200:
            return []
        return resp.json().get("data", {}).get("list", []) or []
    except Exception:
        return []


def _plp_item_to_standard(raw: dict, rank: int, cat_code: str, cat_label: str) -> dict:
    """api2/dp/v2/plp/goods 상품 dict → 표준 포맷 변환"""
    good_no = str(raw.get("goodsNo") or "")
    normal = int(raw.get("normalPrice") or raw.get("price") or 0)
    final_price = int(raw.get("finalPrice") or raw.get("price") or normal)
    disc = int(raw.get("finalDiscount") or raw.get("saleRate") or 0)
    return {
        "rank": rank,
        "id": f"ms-{good_no}",
        "brand": raw.get("brandName") or raw.get("brand") or "",
        "name": raw.get("goodsName") or "",
        "price": normal,
        "originalPrice": normal,
        "discountRate": disc,
        "finalPrice": final_price,
        "category": cat_code,
        "categoryLabel": cat_label,
        "imgUrl": raw.get("thumbnail") or "",
        "productUrl": raw.get("goodsLinkUrl") or (f"https://www.musinsa.com/products/{good_no}" if good_no else ""),
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


def _fetch_client_musinsa_by_category(cat_code: str) -> list:
    """
    client.musinsa.com/api/home/web/v5/pans/ranking 로 카테고리별 랭킹 수집.
    sectionId=201 + categoryCode=xxx 조합이 카테고리별 독립 데이터를 반환함.
    cat_code="" → 전체 (sectionId=200 사용)
    """
    url = "https://client.musinsa.com/api/home/web/v5/pans/ranking"
    headers = {
        "User-Agent": BROWSER_UA,
        "Referer": "https://www.musinsa.com/",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ko-KR,ko;q=0.9",
        "Origin": "https://www.musinsa.com",
    }
    if cat_code:
        params = {
            "storeCode": "musinsa", "sectionId": "201", "gf": "M",
            "categoryCode": cat_code, "ageBand": "AGE_BAND_25",
        }
    else:
        params = {
            "storeCode": "musinsa", "sectionId": "200", "gf": "M",
            "categoryCode": "000", "ageBand": "AGE_BAND_25",
        }
    try:
        r = requests.get(url, params=params, headers=headers, timeout=15)
        if not r.ok:
            return []
        mods = r.json().get("data", {}).get("modules", [])
        return _extract_multicolumn_products(mods)
    except Exception:
        return []


def _fetch_all_musinsa_via_section_api() -> dict:
    """
    sectionId=201 + categoryCode 조합으로 전체 카테고리 랭킹 수집.
    전체는 sectionId=200 사용.
    반환: {cat_code: raw_items}
    """
    # 전체 기준 1위 상품 ID (카테고리별 데이터 검증용)
    all_items = _fetch_client_musinsa_by_category("")
    if not all_items:
        print(f"    [SECTION] 전체 랭킹 수집 실패")
        return {}
    ref_id = all_items[0].get("id")

    results: dict = {"": all_items}
    cat_codes = ["001", "002", "003", "004", "005", "020", "022", "023", "024", "026"]
    found = 0

    for cat_code in cat_codes:
        items = _fetch_client_musinsa_by_category(cat_code)
        if items and items[0].get("id") != ref_id:
            results[cat_code] = items
            found += 1
        else:
            # 전체와 동일하거나 빈 경우: 더 넓은 sectionId 범위 탐색
            for sid in range(202, 220):
                try:
                    url = "https://client.musinsa.com/api/home/web/v5/pans/ranking"
                    r = requests.get(url, params={
                        "storeCode": "musinsa", "sectionId": str(sid), "gf": "M",
                        "categoryCode": cat_code, "ageBand": "AGE_BAND_25",
                    }, headers={
                        "User-Agent": BROWSER_UA, "Referer": "https://www.musinsa.com/",
                        "Accept": "application/json", "Origin": "https://www.musinsa.com",
                    }, timeout=8)
                    if not r.ok:
                        continue
                    mods = r.json().get("data", {}).get("modules", [])
                    sid_items = _extract_multicolumn_products(mods)
                    if sid_items and sid_items[0].get("id") != ref_id:
                        results[cat_code] = sid_items
                        found += 1
                        print(f"    [SECTION] ✓ {cat_code} sectionId={sid}에서 발견")
                        break
                except Exception:
                    continue

    print(f"    [SECTION] {found}개 카테고리 수집 완료 (전체 포함 {len(results)}개)")
    return results


def _probe_ranking_section_ids() -> dict:
    """
    client.musinsa.com API를 탐색하여 카테고리별 랭킹 sectionId 발견.
    반환: {sectionId(int): raw_items} — 전체와 다른 데이터를 반환한 sectionId
    """
    api_url = "https://client.musinsa.com/api/home/web/v5/pans/ranking"
    headers = {
        "User-Agent": BROWSER_UA,
        "Referer": "https://www.musinsa.com/",
        "Accept": "application/json",
        "Origin": "https://www.musinsa.com",
    }

    def call_api(sid, gf, cat_code=""):
        params = {
            "storeCode": "musinsa", "sectionId": str(sid), "gf": gf,
            "categoryCode": cat_code or "000", "ageBand": "AGE_BAND_25",
        }
        try:
            r = requests.get(api_url, params=params, headers=headers, timeout=8)
            if not r.ok:
                return []
            mods = r.json().get("data", {}).get("modules", [])
            return _extract_multicolumn_products(mods)
        except Exception:
            return []

    ref_items = _fetch_via_home_widget_all()
    if not ref_items:
        return {}
    ref_id = ref_items[0].get("id")
    ref_name = ref_items[0].get("info", {}).get("productName", "")[:20]
    print(f"    [PROBE] 기준(sectionId=200,gf=M): {ref_name}")

    results = {}

    # gf=A (전 성별) 와 gf=M 둘 다 탐색 — 실제 무신사 URL은 gf=A 사용
    for gf in ("A", "M"):
        print(f"    [PROBE] sectionId 195-230 탐색 (gf={gf})")
        for sid in range(195, 231):
            if sid == 200:
                continue
            items = call_api(sid, gf)
            if not items:
                continue
            first_id = items[0].get("id")
            if first_id != ref_id and sid not in results:
                names = [it.get("info", {}).get("productName", "")[:25] for it in items[:5]]
                print(f"    [PROBE] ✓ sectionId={sid} gf={gf}: {len(items)}개")
                for i, n in enumerate(names, 1):
                    print(f"      {i}위: {n}")
                results[sid] = items
            time.sleep(0.05)
        if len(results) >= 10:
            break

    return results


def _fetch_all_musinsa_via_playwright() -> dict:
    """
    Playwright로 무신사 랭킹 페이지에 직접 접속하여 카테고리 탭 클릭 후
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
    all_captured_urls: list[str] = []

    def on_response(response):
        nonlocal last_captured
        url = response.url
        if response.status == 200 and "musinsa" in url:
            try:
                body = response.json()
                mods = body.get("data", {}).get("modules", [])
                items = _extract_multicolumn_products(mods)
                if items:
                    last_captured = items
                    all_captured_urls.append(url)
                elif isinstance(body, dict):
                    for key in ["list", "goods", "items", "products"]:
                        val = body.get(key, [])
                        if isinstance(val, list) and len(val) > 5 and isinstance(val[0], dict):
                            if any(k in val[0] for k in ["id", "goodsNo", "itemId"]):
                                all_captured_urls.append(f"{url} [{key}:{len(val)}]")
                                break
            except Exception:
                pass

    page.on("response", on_response)
    results = {}

    try:
        # 탐색할 URL 목록 (geo-redirect 없이 접근 가능한 경로 위주)
        ranking_urls = [
            "https://www.musinsa.com/ranking/best",
            "https://www.musinsa.com/ranking/best?categoryCode=001",
            "https://www.musinsa.com/",
            "https://www.musinsa.com/main/musinsa/recommend?gf=A",
            "https://www.musinsa.com/store/ranking/best",
        ]

        for try_url in ranking_urls:
            last_captured = []
            all_captured_urls.clear()
            try:
                page.goto(try_url, wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(5000)
                landed = page.url

                if "global.musinsa.com" in landed or "choose-location" in landed:
                    print(f"    [PW] {try_url[:55]} → geo-redirect, 스킵")
                    continue

                title = ""
                try:
                    title = page.title()
                except Exception:
                    pass
                print(f"    [PW] {try_url[:60]} → {title[:30]}")

                # __NEXT_DATA__ 에서 상품 추출 시도
                try:
                    next_data_str = page.evaluate(
                        "() => document.getElementById('__NEXT_DATA__')?.textContent || ''"
                    )
                    if next_data_str:
                        next_data = json.loads(next_data_str)
                        nd_products = _search_next_data_products(next_data)
                        if nd_products:
                            print(f"    [PW] __NEXT_DATA__ 상품 {len(nd_products)}개 발견")
                            last_captured = [{"_raw": p} for p in nd_products]
                except Exception:
                    pass

                if last_captured:
                    print(f"    [PW] {len(last_captured)}개 상품 캡처! → {try_url[:40]}")
                    if not results.get(""):
                        results[""] = list(last_captured)
                else:
                    if all_captured_urls:
                        print(f"    [PW] API 호출 감지: {all_captured_urls[:3]}")
                    # 보이는 버튼/탭 목록 확인
                    try:
                        tabs = page.evaluate("""
                            () => [...document.querySelectorAll('button,[role=tab],li,a')]
                                .filter(e=>e.offsetParent)
                                .map(e=>e.textContent.trim())
                                .filter(t=>t.length>0&&t.length<15)
                                .slice(0,20)
                        """)
                        if tabs:
                            print(f"    [PW] 페이지 요소: {tabs[:10]}")
                    except Exception:
                        pass

                # 카테고리 탭 클릭 시도
                for cat_code, cat_label in cat_tab_map:
                    if cat_code in results:
                        continue
                    last_captured = []
                    for sel in [
                        f'button:text-is("{cat_label}")',
                        f'[role="tab"]:text-is("{cat_label}")',
                        f'li:text-is("{cat_label}")',
                        f'a:text-is("{cat_label}")',
                    ]:
                        try:
                            el = page.locator(sel).first
                            if el.is_visible(timeout=1000):
                                el.click()
                                page.wait_for_timeout(2000)
                                if last_captured:
                                    results[cat_code] = list(last_captured)
                                    print(f"    [PW] {cat_label}: {len(last_captured)}개 (탭 클릭)")
                                break
                        except Exception:
                            continue

            except Exception as e:
                print(f"    [PW] {try_url[:50]} 오류: {str(e)[:60]}")

    except Exception as e:
        print(f"    [PW] 오류: {e}")
    finally:
        context.close()

    return results


PRODUCT_KEYS = {"goodsNo", "goodsId", "goodsName", "brandName", "salePrice", "normalPrice"}

def _search_next_data_products(data, depth=0) -> list:
    """__NEXT_DATA__ JSON에서 상품 배열 탐색"""
    if depth > 10:
        return []
    if isinstance(data, list) and len(data) >= 3:
        if data and isinstance(data[0], dict) and (set(data[0].keys()) & PRODUCT_KEYS):
            return data
    if isinstance(data, dict):
        for v in data.values():
            r = _search_next_data_products(v, depth + 1)
            if r:
                return r
    elif isinstance(data, list):
        for item in data:
            r = _search_next_data_products(item, depth + 1)
            if r:
                return r
    return []


# ═══════════════════════════════════════════════════
#  무신사 메인
# ═══════════════════════════════════════════════════

def _diag_category_ranking_endpoints():
    """
    [진단] categoryCode 파라미터가 실제 필터로 작동하는 엔드포인트 탐색.
    여러 URL/파라미터 조합에 대해 categoryCode=001(상의) vs 020(신발) 결과가
    달라지는지 비교 → 다르면 그 엔드포인트가 진짜 카테고리 랭킹 API.
    """
    print("  ===== [진단] 카테고리 랭킹 엔드포인트 탐색 =====")
    headers = {
        "User-Agent": BROWSER_UA,
        "Referer": "https://www.musinsa.com/",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ko-KR,ko;q=0.9",
        "Origin": "https://www.musinsa.com",
    }

    def first3(items):
        out = []
        for it in items[:3]:
            info = it.get("info", {})
            out.append(info.get("productName", "")[:24])
        return out

    # 후보 1: home/web/v5/pans/ranking 에서 sectionId 고정 + categoryCode 변화
    base = "https://client.musinsa.com/api/home/web/v5/pans/ranking"
    for sid in [199, 200, 201, 204, 205, 206, 209, 210]:
        res = {}
        for cc in ["001", "020"]:
            try:
                r = requests.get(base, params={
                    "storeCode": "musinsa", "sectionId": str(sid), "gf": "A",
                    "categoryCode": cc, "ageBand": "AGE_BAND_25",
                }, headers=headers, timeout=10)
                mods = r.json().get("data", {}).get("modules", [])
                res[cc] = _extract_multicolumn_products(mods)
            except Exception as e:
                res[cc] = []
        t1, t20 = first3(res.get("001", [])), first3(res.get("020", []))
        differ = t1 != t20 and t1 and t20
        flag = "★다름★" if differ else "동일"
        print(f"  [진단] sectionId={sid} categoryCode필터 {flag}")
        print(f"         001(상의)={t1}")
        print(f"         020(신발)={t20}")

    # 후보 2: 다른 ranking 경로들
    alt_urls = [
        ("api2/hm-ranking", "https://client.musinsa.com/api2/hm/web/v5/pans/ranking"),
        ("display-v2", "https://client.musinsa.com/api/display/v2/ranking"),
        ("display-v3", "https://client.musinsa.com/api/display/v3/ranking"),
        ("dp-plp", "https://client.musinsa.com/api2/dp/v1/plp/goods"),
    ]
    for label, u in alt_urls:
        try:
            r = requests.get(u, params={
                "storeCode": "musinsa", "gf": "A", "categoryCode": "001",
                "category": "001000", "sectionId": "200", "sortCode": "POPULAR",
                "page": 1, "size": 5,
            }, headers=headers, timeout=10)
            print(f"  [진단] {label}: HTTP {r.status_code} ({u})")
            if r.ok:
                try:
                    print(f"         body앞부분: {json.dumps(r.json())[:200]}")
                except Exception:
                    print(f"         text앞부분: {r.text[:200]}")
        except Exception as e:
            print(f"  [진단] {label}: 오류 {str(e)[:60]}")
    print("  ===== [진단] 종료 =====")


def _rebuild_all_from_subcats(categories: dict, limit: int):
    """개별 서브카테고리 아이템만으로 전체(빈 코드) 목록을 재구성.
    신발/가방 등 제거된 카테고리 상품이 전체 랭킹에 노출되지 않도록 한다.

    참고: 무신사 "전체 인기" 위젯 원본과 카테고리별 랭킹은 서로 다른 소스라
    상품 ID가 거의 겹치지 않는다. 그래서 원본 전체 순서를 그대로 쓰면서
    비의류만 걸러내는 방식은 불가능하고(교집합이 없어 다 걸러지거나 다
    남아버림), 카테고리별 순위를 병합하는 이 방식이 "의류만" 보장할 수 있는
    유일한 방법이다. 다만 카테고리 간 비교 가능한 공통 순위 지표가 없어서,
    병합 순서는 "각 카테고리 내부 순위" 기준이 된다(진짜 전체 인기순은 아님)."""
    seen_ids = set()
    merged = []
    for code, items in categories.items():
        if not code:  # 전체 자신은 건너뜀
            continue
        for item in items:
            uid = item.get("id") or item.get("name", "")
            if uid not in seen_ids:
                seen_ids.add(uid)
                merged.append(item)
    if merged:
        merged.sort(key=lambda x: x.get("rank", 9999))
        # 순위를 1부터 재부여
        for idx, item in enumerate(merged[:limit]):
            item = dict(item)
            item["rank"] = idx + 1
            merged[idx] = item
        categories[""] = merged[:limit]


def fetch_musinsa() -> dict:
    print("▶ 무신사 랭킹 수집 시작...")

    # ── 방법 0: 실제 카테고리 상품 목록(PLP) API ──────────────────────────
    # www.musinsa.com/category/<code>/goods?sortCode=POPULAR 페이지가 쓰는
    # 진짜 API. 세부 서브카테고리까지 정확히 필터링되고 30개 이상도 지원한다
    # (직접 Playwright로 네트워크 요청을 확인해 검증 완료).
    categories0: dict = {}

    pool_all = _fetch_client_musinsa_by_category("")
    if pool_all:
        categories0[""] = _multicolumn_to_items(pool_all, "", "전체")
        print(f"    ✓ 전체: {len(categories0[''])}개")
    time.sleep(0.3)

    for cat in MUSINSA_CATEGORIES:
        cat_code = cat["code"]
        cat_label = cat["label"]
        if not cat_code:
            continue  # 전체는 위에서 이미 처리
        real_code = MUSINSA_REAL_CATEGORY_CODE.get(cat_code, cat_code)
        raw = _fetch_musinsa_plp_goods(real_code, size=LIMIT)
        if raw:
            items = [_plp_item_to_standard(r, idx + 1, cat_code, cat_label) for idx, r in enumerate(raw[:LIMIT])]
            categories0[cat_code] = items
            print(f"    ✓ [PLP] {cat_label}({real_code}): {len(items)}개")
        else:
            categories0[cat_code] = []
            print(f"    [WARN] {cat_label}({real_code}): 데이터 없음")
        time.sleep(0.3)

    if categories0.get(""):
        _rebuild_all_from_subcats(categories0, LIMIT)
        total0 = sum(len(v) for v in categories0.values())
        print(f"  ✓ 무신사 {total0}개 수집 완료 (PLP 방식, {len(categories0)}개 카테고리)")
        return categories0

    print("  [WARN] PLP 방식 실패 → 기존 폴백 체인 시도")

    # ── 방법 1: 카테고리별 전용 API (레거시, 보통 404로 실패함) ──────────
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
        _rebuild_all_from_subcats(categories, LIMIT)
        total = sum(len(v) for v in categories.values())
        print(f"  ✓ 무신사 {total}개 수집 완료 (카테고리별 API)")
        return categories

    # ── 방법 2: Playwright ──────────────────────────────
    print("  [WARN] 카테고리 API 무효 → Playwright 랭킹 페이지 시도")
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

    # ── 방법 4: sectionId 범위 탐색 + 카테고리 매핑 ──────────────────
    # probe 로그(gf=A)로 식별한 sectionId → categoryCode 매핑:
    #   205→상의(001), 204→아우터(002), 209→바지(003), 207→원피스/스커트(004)
    #   201→스포츠(005), 206→신발(020), 210→가방(022), 202→패션잡화(024)
    #   023(시계/쥬얼리), 026(화장품/향수)는 전용 섹션 없음
    MUSINSA_SECTION_MAP: dict[int, str] = {
        205: "001",  # 상의
        204: "002",  # 아우터
        209: "003",  # 바지
        207: "004",  # 원피스/스커트
        201: "005",  # 스포츠
        206: "020",  # 신발
        210: "022",  # 가방
        202: "024",  # 패션잡화
    }
    cat_to_section: dict[str, int] = {v: k for k, v in MUSINSA_SECTION_MAP.items()}

    cat_codes_needed = [c["code"] for c in MUSINSA_CATEGORIES if c["code"] and c["code"] not in playwright_results]
    probe_results: dict[int, list] = {}
    if cat_codes_needed:
        print(f"  [PROBE] sectionId 탐색 시작 ({len(cat_codes_needed)}개 카테고리 미수집)")
        probe_results = _probe_ranking_section_ids()
        print(f"  [PROBE] {len(probe_results)}개 섹션 발견: {sorted(probe_results.keys())}")

    categories = {}
    for cat in MUSINSA_CATEGORIES:
        cat_code = cat["code"]
        cat_label = cat["label"]

        raw_items = playwright_results.get(cat_code)
        if raw_items:
            items = _multicolumn_to_items(raw_items, cat_code, cat_label)
            if items:
                categories[cat_code] = items
                print(f"    ✓ [PW] {cat_label}: {len(items)}개")
                continue

        if not cat_code and fallback_all:
            categories[cat_code] = _multicolumn_to_items(fallback_all, cat_code, cat_label)
            print(f"    ✓ [홈위젯] 전체: {len(categories[cat_code])}개")
        elif cat_code and cat_code in cat_to_section:
            sec_id = cat_to_section[cat_code]
            sec_items = probe_results.get(sec_id, [])
            if sec_items:
                categories[cat_code] = _multicolumn_to_items(sec_items, cat_code, cat_label)
                print(f"    ✓ [PROBE] {cat_label}(sectionId={sec_id}): {len(categories[cat_code])}개")
            else:
                categories[cat_code] = []
                print(f"    [빈목록] {cat_label}: sectionId={sec_id} 데이터 없음")
        else:
            categories[cat_code] = []
            print(f"    [빈목록] {cat_label}: 전용 섹션 없음")

    _rebuild_all_from_subcats(categories, LIMIT)
    total = sum(len(v) for v in categories.values())
    print(f"  ✓ 무신사 {total}개 수집 완료 ({len(categories)}개 카테고리)")
    return categories


# ═══════════════════════════════════════════════════
#  29CM API
# ═══════════════════════════════════════════════════

# 29CM는 무신사와 동일한 의류 카테고리 체계로 노출한다.
# 29CM 베스트 API는 카테고리별 전용 코드가 불안정하므로, 넓은 베스트 풀을
# 수집한 뒤 상품명 키워드로 무신사와 동일한 카테고리로 분류한다.
CM29_CATEGORIES = [
    {"code": "",       "label": "전체"},
    {"code": "001001", "label": "반팔티"},
    {"code": "001002", "label": "긴팔티"},
    {"code": "001003", "label": "맨투맨/스웨트"},
    {"code": "001004", "label": "후드티"},
    {"code": "001005", "label": "셔츠/블라우스"},
    {"code": "001006", "label": "니트/스웨터"},
    {"code": "002",    "label": "아우터"},
    {"code": "003001", "label": "데님 팬츠"},
    {"code": "003002", "label": "슬랙스"},
    {"code": "003003", "label": "트레이닝/조거"},
    {"code": "003005", "label": "반바지"},
]

# 넓은 풀 수집을 위한 29CM 대분류 코드(전체/여성의류/남성의류)
CM29_POOL_LARGE_CODES = [None, "268100100", "272100100"]

CM29_LABEL_BY_CODE = {c["code"]: c["label"] for c in CM29_CATEGORIES}


def _classify_29cm_category(name: str) -> str:
    """29CM 상품명을 무신사 카테고리 코드로 분류. 의류가 아니면 ''(미분류)."""
    if not name:
        return ""
    n = name.lower()

    def has(*kws):
        return any(k in n for k in kws)

    # 1) 후드 (집업 후드 포함)
    if has("후드", "hood"):
        return "001004"
    # 2) 맨투맨 / 스웨트셔츠
    if has("맨투맨", "mtm", "스웨트셔츠", "스웨트 셔츠", "sweatshirt", "크루넥 스웨트"):
        return "001003"
    # 3) 니트 / 스웨터 / 가디건
    if has("니트", "knit", "스웨터", "sweater", "가디건", "cardigan", "풀오버", "pullover"):
        return "001006"
    # 4) 셔츠 / 블라우스 (티셔츠 제외)
    if has("블라우스", "blouse") or ("셔츠" in name and "티셔츠" not in name) or "shirt" in n.replace("t-shirt", "").replace("tshirt", ""):
        return "001005"
    # 5) 반팔 티
    if has("반팔", "반소매", "half sleeve", "short sleeve", "short-sleeve", "s/s", "1/2"):
        return "001001"
    # 6) 긴팔 티
    if has("긴팔", "긴소매", "long sleeve", "long-sleeve", "l/s"):
        return "001002"
    # 7) 아우터 (자켓/코트/패딩 등) — 바지 분류보다 먼저 체크
    if has("자켓", "재킷", "jacket", "점퍼", "점버", "jumper", "코트", "coat",
           "패딩", "padding", "야상", "블루종", "blouson", "바람막이", "윈드브레이커",
           "windbreaker", "후리스", "플리스", "fleece", "블레이저", "blazer",
           "파카", "parka", "다운", "down", "베스트", "vest", "조끼", "집업", "zip-up", "zip up"):
        return "002"
    # 8) 슬랙스
    if has("슬랙스", "slacks", "정장 바지", "정장바지"):
        return "003002"
    # 9) 트레이닝 / 조거
    if has("조거", "jogger", "트레이닝", "training", "트랙 팬츠", "트랙팬츠",
           "track pants", "스웨트팬츠", "스웨트 팬츠", "sweatpants"):
        return "003003"
    # 10) 반바지 / 쇼츠
    if has("반바지", "숏팬츠", "숏 팬츠", "쇼츠", "쇼트", "shorts", "하프팬츠",
           "하프 팬츠", "버뮤다", "bermuda"):
        return "003005"
    # 11) 데님 팬츠 (데님 아우터는 위에서 처리됨)
    if has("데님", "denim", "청바지", "진 팬츠", "jean", "jeans"):
        return "003001"
    # 12) 일반 티셔츠/티 → 여름 기준 반팔티로 분류
    if has("티셔츠", "t-shirt", "tshirt", "tee", "반팔티", "튜블러", "tubular"):
        return "001001"
    # 한글 단어가 '티'로 끝나는 경우(링거티/웨일티 등) → 티셔츠로 간주
    if re.search(r"[가-힣]티(?:[\s_/()\[\]]|$)", name):
        return "001001"
    return ""


# 29CM 자체 category3Name(세부) → 무신사 카테고리 코드. 상품명에 키워드가 없어도
# 29CM이 이미 분류해둔 공식 카테고리라 상품명 키워드 분류보다 신뢰도가 높음.
# 상의/아우터/니트류는 category2Name과 무관하게 category3Name만으로 특정 가능.
CM29_TOP_CATEGORY3_MAP = {
    "반소매 티셔츠": "001001",
    "긴소매 티셔츠": "001002",
    "반소매 셔츠": "001005",
    "긴소매 셔츠": "001005",
    "셔츠": "001005",
    "블라우스": "001005",
    "폴로셔츠": "001005",
    "맨투맨": "001003",
    "스웨트셔츠": "001003",
    "후드": "001004",
    "후드 집업": "001004",
    "니트": "001006",
    "기타 니트": "001006",
    "카디건": "001006",
    "스웨터": "001006",
    "베스트": "002",
    "블레이저": "002",
    "블루종": "002",
    "기타 아우터": "002",
    "패딩": "002",
    "코트": "002",
    "자켓": "002",
}

# 바지류 category3Name("데님", "쇼트" 등)은 스커트/원피스에도 동일하게 쓰이므로
# category2Name이 실제로 하의/바지 계열일 때만 적용 (예: "스커트"+"데님" = 데님 스커트, 바지 아님)
CM29_BOTTOM_CATEGORY2_CONTEXT = {"하의", "바지"}
CM29_BOTTOM_CATEGORY3_MAP = {
    "슬랙스": "003002",
    "정장 바지": "003002",
    "데님 팬츠": "003001",
    "데님": "003001",
    "트레이닝": "003003",
    "트레이닝 팬츠": "003003",
    "조거 팬츠": "003003",
    "쇼트": "003005",
    "숏팬츠": "003005",
}


def _classify_29cm_by_category_info(item: dict) -> str:
    """29CM이 자체 부여한 category2/3Name을 우선 사용, 매칭 안 되면 빈 문자열 반환
    (호출부에서 상품명 키워드 분류로 폴백)."""
    info = (item.get("frontCategoryInfo") or [{}])[0]
    cat2 = (info.get("category2Name") or "").strip()
    cat3 = (info.get("category3Name") or "").strip()
    if cat3 in CM29_TOP_CATEGORY3_MAP:
        return CM29_TOP_CATEGORY3_MAP[cat3]
    if cat2 in CM29_BOTTOM_CATEGORY2_CONTEXT and cat3 in CM29_BOTTOM_CATEGORY3_MAP:
        return CM29_BOTTOM_CATEGORY3_MAP[cat3]
    if cat2 == "니트웨어":
        return "001006"
    if cat2 == "아우터":
        return "002"
    return ""


def _fetch_29cm_recommend(large_code) -> list:
    """
    recommend-api.29cm.co.kr/api/v4/best/items 로 카테고리별 베스트 수집.
    large_code=None -> 전체 베스트.
    """
    url = "https://recommend-api.29cm.co.kr/api/v4/best/items"
    headers = {
        "User-Agent": BROWSER_UA,
        "Referer": "https://www.29cm.co.kr/",
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://www.29cm.co.kr",
    }
    # 실제 29CM 베스트 페이지가 호출하는 파라미터: categoryList + periodSort
    params = {"periodSort": "NOW", "limit": 100, "offset": 0}
    if large_code:
        params["categoryList"] = large_code
    resp = requests.get(url, params=params, headers=headers, timeout=20)
    resp.raise_for_status()
    return resp.json().get("data", {}).get("content", []) or []


def _build_29cm_item(item: dict, rank: int, cat_code: str, cat_label: str) -> dict:
    item_no = str(item.get("itemNo", ""))
    name = item.get("itemName", "")
    brand = item.get("frontBrandNameKor") or item.get("frontBrandNameEng") or ""
    original = int(item.get("consumerPrice") or 0)
    final = int(item.get("lastSalePrice") or original)
    disc = int(item.get("lastSalePercent") or 0)
    img = item.get("imageUrl", "") or ""
    if img.startswith("/"):
        img = "https://img.29cm.co.kr" + img
    return {
        "rank": rank,
        "id": f"cm-{item_no}",
        "brand": brand,
        "name": name,
        "price": original,
        "originalPrice": original,
        "discountRate": disc,
        "finalPrice": final,
        "category": cat_code,
        "categoryLabel": cat_label,
        "imgUrl": img,
        "productUrl": f"https://product.29cm.co.kr/catalog/{item_no}",
        "change": None,
    }


def fetch_29cm() -> dict:
    print("▶ 29CM 랭킹 수집 시작...")

    # 1) 넓은 베스트 풀 수집 (전체 + 여성의류 + 남성의류)
    seen = set()
    pool = []  # 최초 등장 순서 = 베스트 우선순위
    for large in CM29_POOL_LARGE_CODES:
        try:
            raw_items = _fetch_29cm_recommend(large)
        except Exception as e:
            print(f"    [WARN] 29CM 풀 수집 실패(large={large}): {e}")
            raw_items = []
        for item in raw_items:
            no = str(item.get("itemNo", ""))
            if no and no not in seen:
                seen.add(no)
                pool.append(item)
        time.sleep(0.4)

    print(f"    · 베스트 풀 {len(pool)}개 수집")

    # 2) 29CM 자체 카테고리 정보 우선 사용, 없을 때만 상품명 키워드로 분류.
    #    (category info가 있는데 매칭이 안 되면 스커트/원피스 등 무신사 미대응 상품일
    #     확률이 높으므로, 이 경우 키워드로 재추측하지 않고 그대로 제외한다.
    #     예: "SLIT DENIM MAXI SKIRT"는 category3Name="데님"이라 바지로 오분류될 뻔했으나,
    #     category2Name="스커트"라 제외되고, 여기서 키워드 폴백을 타면 상품명의 "denim"
    #     때문에 다시 데님 팬츠로 잘못 분류되는 문제가 있었음.)
    buckets = {c["code"]: [] for c in CM29_CATEGORIES if c["code"]}
    clothing_ordered = []  # 전체(의류) 재구성용
    for item in pool:
        has_category_info = bool(item.get("frontCategoryInfo"))
        if has_category_info:
            code = _classify_29cm_by_category_info(item)
        else:
            code = _classify_29cm_category(item.get("itemName", ""))
        if not code:
            continue  # 가방/신발/원피스 등 무신사 미대응 → 제외
        b = buckets[code]
        b.append(_build_29cm_item(item, len(b) + 1, code, CM29_LABEL_BY_CODE[code]))
        clothing_ordered.append(item)

    categories = {}
    categories[""] = [
        _build_29cm_item(item, idx + 1, "", "전체")
        for idx, item in enumerate(clothing_ordered[:LIMIT])
    ]
    for code, items in buckets.items():
        categories[code] = items[:LIMIT]

    nonempty = sum(1 for v in categories.values() if v)
    total = sum(len(v) for v in categories.values())
    print(f"  ✓ 29CM {total}개 수집 완료 ({nonempty}/{len(categories)}개 카테고리)")
    for c in CM29_CATEGORIES:
        print(f"      [{c['label']}] {len(categories.get(c['code'], []))}개")
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


KINLOCK_BRAND = "커넥트킨록"
KINLOCK_STATS_PATH = DATA_DIR / "kinlock_stats.json"


def update_kinlock_exposure(musinsa_cats: dict):
    """무신사 카테고리 TOP 50 안에 커넥트킨록 상품이 있으면 노출 회차 +1."""
    # 전체('')는 서브카테고리 합산이므로 서브카테고리만 체크
    appeared = any(
        any(item.get("brand") == KINLOCK_BRAND for item in items)
        for code, items in musinsa_cats.items()
        if code != ""
    )
    # 서브카테고리 데이터가 없으면 전체로 폴백 체크
    if not appeared:
        appeared = any(
            item.get("brand") == KINLOCK_BRAND
            for item in musinsa_cats.get("", [])
        )

    stats = {"exposureCount": 0, "history": []}
    if KINLOCK_STATS_PATH.exists():
        try:
            with open(KINLOCK_STATS_PATH, encoding="utf-8") as f:
                stats = json.load(f)
        except Exception:
            pass

    now_str = datetime.now(timezone.utc).isoformat()
    if appeared:
        stats["exposureCount"] = stats.get("exposureCount", 0) + 1
        stats.setdefault("history", []).append(now_str)
        print(f"  ⭐ 커넥트킨록 노출 감지 → 누적 {stats['exposureCount']}회차")
    else:
        print(f"  — 커넥트킨록 미노출 (누적 {stats.get('exposureCount', 0)}회차)")

    stats["lastChecked"] = now_str
    stats["lastAppeared"] = appeared
    with open(KINLOCK_STATS_PATH, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    print(f"  💾 저장: {KINLOCK_STATS_PATH}")


# ═══════════════════════════════════════════════════
#  가격 통계 (카테고리별 평균/최저/최고가)
# ═══════════════════════════════════════════════════

def _fetch_musinsa_prices_extra(cat_code: str, existing_prices: list) -> list:
    """랭킹 API 2~4페이지 추가 수집으로 가격 샘플 확대"""
    headers = {
        "User-Agent": BROWSER_UA,
        "Referer": "https://www.musinsa.com/ranking/best",
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://www.musinsa.com",
    }
    prices = list(existing_prices)
    for page in range(2, 5):
        try:
            params: dict = {"page": page, "size": 30, "storeCode": "musinsa"}
            if cat_code:
                params["contentsCode"] = cat_code
            r = requests.get(
                "https://api.musinsa.com/api2/json/rank/goods",
                params=params, headers=headers, timeout=12
            )
            if not r.ok:
                break
            data = r.json()
            raw = (
                data.get("data", {}).get("list")
                or data.get("data", {}).get("goods")
                or data.get("list")
                or []
            )
            if not raw:
                break
            for item in raw:
                p = int(item.get("salePrice") or item.get("finalPrice") or item.get("normalPrice") or 0)
                if p > 0:
                    prices.append(p)
            time.sleep(0.4)
        except Exception:
            break
    return prices


def compute_price_stats(categories_data: dict, categories_meta: list) -> dict:
    """카테고리별 가격 통계 계산. 가능하면 API 추가 페이지로 샘플 확장."""
    print("▶ 가격 통계 계산 중...")
    stats = {}
    api_works = _MUSINSA_API_WORKING  # 이미 검증된 상태 재사용

    for cat in categories_meta:
        code = cat["code"]
        label = cat["label"]

        items = categories_data.get(code, [])
        prices = [
            i.get("finalPrice") or i.get("price") or 0
            for i in items
        ]
        prices = [p for p in prices if p > 0]

        # API가 동작 중이면 추가 페이지 수집으로 샘플 확장
        if api_works and code:
            prices = _fetch_musinsa_prices_extra(code, prices)
            time.sleep(0.3)

        if not prices:
            continue

        stats[code] = {
            "label": label,
            "avg": round(sum(prices) / len(prices)),
            "min": min(prices),
            "max": max(prices),
            "count": len(prices),
        }
        print(f"  {label}: 평균 {stats[code]['avg']:,}원 ({len(prices)}개 샘플)")

    return stats


def compute_29cm_price_stats(categories_data: dict, categories_meta: list) -> dict:
    """29CM 카테고리별 가격 통계 (이미 100개 수집됨)"""
    stats = {}
    for cat in categories_meta:
        code = cat["code"]
        label = cat["label"]
        items = categories_data.get(code, [])
        prices = [
            i.get("finalPrice") or i.get("price") or 0
            for i in items
        ]
        prices = [p for p in prices if p > 0]
        if not prices:
            continue
        stats[code] = {
            "label": label,
            "avg": round(sum(prices) / len(prices)),
            "min": min(prices),
            "max": max(prices),
            "count": len(prices),
        }
    return stats


# ═══════════════════════════════════════════════════
#  브랜드 카테고리별 가격 통계
# ═══════════════════════════════════════════════════

COMPARE_BRANDS = [
    {"name": "커넥트킨록", "code": "kinloch"},
]

# 브랜드 API 수집 실패 시 사용할 폴백 데이터 (수동 조사 기준)
BRAND_FALLBACK = {
    "커넥트킨록": {
        "001001": {"label": "반소매 티셔츠", "avg": 48000,  "min": 30000,  "max": 65000,  "count": 3},
        "001002": {"label": "긴소매 티셔츠", "avg": 55000,  "min": 45000,  "max": 65000,  "count": 2},
        "001003": {"label": "맨투맨/스웨트", "avg": 75000,  "min": 65000,  "max": 89000,  "count": 2},
        "001005": {"label": "셔츠/블라우스", "avg": 77000,  "min": 55000,  "max": 98000,  "count": 2},
        "002":    {"label": "아우터",        "avg": 264000, "min": 34640,  "max": 392390, "count": 7},
        "003001": {"label": "데님 팬츠",     "avg": 89000,  "min": 79000,  "max": 98000,  "count": 2},
        "003002": {"label": "슬랙스",        "avg": 71100,  "min": 71100,  "max": 71100,  "count": 1},
        "003005": {"label": "반바지",        "avg": 55000,  "min": 45000,  "max": 65000,  "count": 1},
    },
}

def fetch_brand_category_prices(brand_code: str, brand_name: str, categories: list) -> dict:
    """
    무신사 브랜드 상품 검색 API로 카테고리별 평균가 수집.
    반환: {cat_code: {label, avg, min, max, count}}
    """
    print(f"  ▶ 브랜드 [{brand_name}] 카테고리별 가격 수집...")
    headers = {
        "User-Agent": BROWSER_UA,
        "Referer": f"https://www.musinsa.com/brands/{brand_code}",
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://www.musinsa.com",
    }

    def _fetch_brand_goods(cat_code: str, page: int = 1) -> list:
        """브랜드 상품 목록 수집 (여러 엔드포인트 시도)"""
        # 방법1: 무신사 PLP API
        endpoints = [
            {
                "url": "https://api.musinsa.com/api2/json/plp/goods",
                "params": {
                    "brand": brand_code,
                    "sortCode": "pop_category",
                    "page": page, "size": 60,
                    **({"category": cat_code} if cat_code else {}),
                },
            },
            {
                "url": f"https://www.musinsa.com/brands/{brand_code}/goods",
                "params": {
                    "sortCode": "pop_category",
                    "page": page, "size": 60,
                    **({"categoryCode": cat_code} if cat_code else {}),
                },
            },
        ]
        for ep in endpoints:
            try:
                r = requests.get(ep["url"], params=ep["params"], headers=headers, timeout=12)
                if not r.ok:
                    continue
                data = r.json()
                # 다양한 응답 구조 탐색
                goods = (
                    data.get("data", {}).get("list")
                    or data.get("data", {}).get("goods")
                    or data.get("list")
                    or data.get("goods")
                    or []
                )
                if goods:
                    return goods
            except Exception:
                continue
        return []

    stats = {}
    for cat in categories:
        code = cat["code"]
        label = cat["label"]
        prices = []
        for page in range(1, 4):
            goods = _fetch_brand_goods(code, page)
            if not goods:
                break
            for g in goods:
                p = int(
                    g.get("salePrice") or g.get("finalPrice")
                    or g.get("goodsSalePrice") or g.get("price") or 0
                )
                if p > 0:
                    prices.append(p)
            if len(goods) < 30:
                break
            time.sleep(0.3)

        if prices:
            stats[code] = {
                "label": label,
                "avg": round(sum(prices) / len(prices)),
                "min": min(prices),
                "max": max(prices),
                "count": len(prices),
            }
            print(f"    {label}: 평균 {stats[code]['avg']:,}원 ({len(prices)}개)")
        else:
            print(f"    {label}: 데이터 없음")
        time.sleep(0.4)

    return stats


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
        price_stats = compute_price_stats(musinsa_cats, MUSINSA_CATEGORIES)
        brand_price_stats = {}
        for brand in COMPARE_BRANDS:
            fetched = fetch_brand_category_prices(
                brand["code"], brand["name"], MUSINSA_CATEGORIES
            )
            # API 수집 실패(빈 결과) 시 폴백 데이터 사용
            if not fetched and brand["name"] in BRAND_FALLBACK:
                print(f"    [폴백] {brand['name']} 하드코딩 데이터 사용")
                fetched = BRAND_FALLBACK[brand["name"]]
            brand_price_stats[brand["name"]] = fetched
            time.sleep(1)
        save("musinsa", {
            "platform": "musinsa",
            "updatedAt": datetime.now(timezone.utc).isoformat(),
            "items": musinsa_cats.get("", []),
            "categories": musinsa_cats,
            "priceStats": price_stats,
            "brandPriceStats": brand_price_stats,
        })
        update_kinlock_exposure(musinsa_cats)
    else:
        print("  [WARN] 무신사 데이터 없음 - 기존 파일 유지")

    time.sleep(2)

    old_29cm_cats = load_existing_categories("29cm")
    cm29_cats = fetch_29cm()
    if cm29_cats and any(cm29_cats.values()):
        cm29_cats = compute_rank_changes_categories(cm29_cats, old_29cm_cats)
        cm29_price_stats = compute_29cm_price_stats(cm29_cats, CM29_CATEGORIES)
        save("29cm", {
            "platform": "29cm",
            "updatedAt": datetime.now(timezone.utc).isoformat(),
            "items": cm29_cats.get("", []),
            "categories": cm29_cats,
            "priceStats": cm29_price_stats,
        })
    else:
        print("  [WARN] 29CM 데이터 없음 - 기존 파일 유지")

    _close_playwright()

    print(f"\n{'='*50}")
    print(f"  ✅ 완료: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    main()

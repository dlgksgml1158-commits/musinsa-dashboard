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

    total = sum(len(v) for v in categories.values())
    print(f"  ✓ 무신사 {total}개 수집 완료 ({len(categories)}개 카테고리)")
    return categories


# ═══════════════════════════════════════════════════
#  29CM API
# ═══════════════════════════════════════════════════

CM29_CATEGORIES = [
    {"code": "",        "label": "전체",      "largeCode": None},
    {"code": "women",   "label": "여성의류",  "largeCode": "268100100"},
    {"code": "men",     "label": "남성의류",  "largeCode": "272100100"},
    {"code": "shoes",   "label": "신발",      "largeCode": "270100100"},
    {"code": "bag",     "label": "가방",      "largeCode": "269100100"},
    {"code": "acc",     "label": "액세서리",  "largeCode": "271100100"},
    {"code": "jewelry", "label": "주얼리",    "largeCode": "305100100"},
    {"code": "beauty",  "label": "뷰티",      "largeCode": "266100100"},
    {"code": "life",    "label": "라이프",    "largeCode": "292100100"},
]


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
    print("▶ 29CM \ub7ad\ud0b9 \uc218\uc9d1 \uc2dc\uc791...")

    categories = {}
    for cat in CM29_CATEGORIES:
        code = cat["code"]
        label = cat["label"]
        large = cat["largeCode"]
        try:
            raw_items = _fetch_29cm_recommend(large)
            result = [
                _build_29cm_item(item, idx + 1, code, label)
                for idx, item in enumerate(raw_items[:LIMIT])
            ]
            categories[code] = result
            print(f"    ✓ [{label}] {len(result)}\uac1c")
        except Exception as e:
            print(f"    [WARN] 29CM [{label}] \uc2e4\ud328: {e}")
            categories[code] = []
        time.sleep(0.4)

    total = sum(len(v) for v in categories.values())
    print(f"  ✓ 29CM {total}\uac1c \uc218\uc9d1 \uc644\ub8cc ({len(categories)}\uac1c \uce74\ud14c\uace0\ub9ac)")
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
        "001": {"label": "상의", "avg": 53000, "min": 30000, "max": 77000, "count": 6},
        "002": {"label": "아우터", "avg": 264000, "min": 34640, "max": 392390, "count": 7},
        "003": {"label": "바지", "avg": 71100, "min": 71100, "max": 71100, "count": 1},
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

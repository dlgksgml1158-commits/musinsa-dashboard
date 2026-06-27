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

def _probe_musinsa_category_endpoint() -> str:
    """
    무신사 카테고리 랭킹 API 탐색 — 상의(001)로 테스트해서
    전체(sectionId=200)와 다른 1위 상품을 반환하는 첫 번째 엔드포인트를 반환.
    """
    headers_json = {
        "User-Agent": BROWSER_UA,
        "Referer": "https://www.musinsa.com/",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ko-KR,ko;q=0.9",
        "Origin": "https://www.musinsa.com",
    }

    # sectionId=200 전체 1위 상품명 (비교 기준)
    ref_name = ""
    try:
        r = requests.get(
            "https://client.musinsa.com/api/home/web/v5/pans/ranking",
            params={"storeCode":"musinsa","sectionId":"200","gf":"M","categoryCode":"000","ageBand":"AGE_BAND_25"},
            headers=headers_json, timeout=10,
        )
        mods = r.json().get("data",{}).get("modules",[])
        for mod in mods:
            if mod.get("type") == "MULTICOLUMN":
                items = [i for i in mod.get("items",[]) if i.get("type")=="PRODUCT_COLUMN"]
                if items:
                    ref_name = items[0].get("info",{}).get("productName","")
                    break
    except Exception:
        pass
    print(f"  [PROBE] 전체 기준 1위: {ref_name[:30]}")

    # sec200 + categoryCode=001 modules 구조 상세 디버그
    try:
        r2 = requests.get(
            "https://client.musinsa.com/api/home/web/v5/pans/ranking",
            params={"storeCode":"musinsa","sectionId":"200","gf":"M","categoryCode":"001","ageBand":"AGE_BAND_25"},
            headers=headers_json, timeout=10,
        )
        mods2 = r2.json().get("data",{}).get("modules",[])
        print(f"  [PROBE-DEBUG] sec200+cat001: modules={len(mods2)}개")
        for i, m in enumerate(mods2[:5]):
            items2 = m.get("items", [])
            print(f"  [PROBE-DEBUG]   mod[{i}] type={m.get('type')} items={len(items2)}")
            if items2:
                print(f"  [PROBE-DEBUG]     item[0] type={items2[0].get('type')} keys={list(items2[0].keys())[:8]}")
    except Exception as e:
        print(f"  [PROBE-DEBUG] sec200+cat001 오류: {e}")

    candidates = [
        # sectionId 변형
        ("sec200-cat001-ageM",
         "https://client.musinsa.com/api/home/web/v5/pans/ranking",
         {"storeCode":"musinsa","sectionId":"200","gf":"M","categoryCode":"001","ageBand":"AGE_BAND_25"}),
        ("sec200-cat001-ageA",
         "https://client.musinsa.com/api/home/web/v5/pans/ranking",
         {"storeCode":"musinsa","sectionId":"200","gf":"A","categoryCode":"001","ageBand":"AGE_BAND_25"}),
        ("sec200-cat001-noage",
         "https://client.musinsa.com/api/home/web/v5/pans/ranking",
         {"storeCode":"musinsa","sectionId":"200","gf":"A","categoryCode":"001"}),
        ("sec201",
         "https://client.musinsa.com/api/home/web/v5/pans/ranking",
         {"storeCode":"musinsa","sectionId":"201","gf":"M","ageBand":"AGE_BAND_25"}),
        ("sec202",
         "https://client.musinsa.com/api/home/web/v5/pans/ranking",
         {"storeCode":"musinsa","sectionId":"202","gf":"M","ageBand":"AGE_BAND_25"}),
        ("sec210",
         "https://client.musinsa.com/api/home/web/v5/pans/ranking",
         {"storeCode":"musinsa","sectionId":"210","gf":"M","ageBand":"AGE_BAND_25"}),
        # 검색 API (인기순)
        ("search-popular",
         "https://search.musinsa.com/api/goods/v1/list",
         {"storeCode":"musinsa","categoryCode":"001","sort":"POPULAR","size":30,"page":1}),
        ("search-ranking",
         "https://search.musinsa.com/api/goods/v1/list",
         {"storeCode":"musinsa","categoryCode":"001","sort":"RANKING","size":30,"page":1}),
        # 상품 랭킹 API 변형
        ("app-rank-goods",
         "https://api.musinsa.com/api2/json/rank/goods",
         {"cate":"001","price":"","fromSise":"","toSise":"","page":1,"pageSize":30}),
        ("app2-rank-goods",
         "https://api2.musinsa.com/api2/json/rank/goods",
         {"cate":"001","page":1,"pageSize":30}),
        # ranking 페이지 HTML (변형 URL 시도)
        ("ranking-html-v2",
         "https://www.musinsa.com/store/ranking/best",
         {"categoryCode":"001","period":"now","gf":"A","display_cnt":30}),
        ("ranking-html-store",
         "https://www.musinsa.com/store/best",
         {"categoryCode":"001","gf":"A","display":30}),
        # goods 리스트 API
        ("goods-list-pop",
         "https://www.musinsa.com/app/goods/lists",
         {"category":"001","sort":"POPULAR","display_cnt":30}),
        ("client-goods-list",
         "https://client.musinsa.com/api/goods/v2/list",
         {"storeCode":"musinsa","categoryCode":"001","sort":"POPULAR","size":30}),
        # 새 랭킹 URL 후보
        ("ranking-no-best",
         "https://www.musinsa.com/ranking",
         {"categoryCode":"001","period":"now","gf":"A"}),
        ("store-ranking",
         "https://www.musinsa.com/store/ranking",
         {"categoryCode":"001","period":"now","gf":"A"}),
        ("best-items",
         "https://www.musinsa.com/best",
         {"categoryCode":"001","gf":"A"}),
        # client API 새 경로
        ("client-ranking-best",
         "https://client.musinsa.com/api/ranking/v2/best",
         {"storeCode":"musinsa","categoryCode":"001","size":30,"gf":"A"}),
        ("client-pans-v6",
         "https://client.musinsa.com/api/home/web/v6/pans/ranking",
         {"storeCode":"musinsa","sectionId":"200","categoryCode":"001","gf":"A"}),
    ]

    for label, url, params in candidates:
        try:
            r = requests.get(url, params=params, headers=headers_json, timeout=8)
            if not r.ok:
                print(f"  [PROBE] {label}: HTTP {r.status_code}")
                continue
            data = r.json()
            # 응답에서 상품명 추출 시도
            goods = _search_product_list(data)
            first = ""
            if goods:
                first = (goods[0].get("goodsName") or goods[0].get("productName")
                         or goods[0].get("name") or "")
            # 전체와 다른 결과인지 확인
            if first and first != ref_name:
                print(f"  [PROBE] ✓ 카테고리 데이터 발견! {label}: 1위={first[:30]}")
                return label + "|" + url + "|" + json.dumps(params)
            elif first:
                print(f"  [PROBE] {label}: 응답OK BUT 전체와 동일 1위={first[:30]}")
            else:
                # 데이터 구조 디버그 로그 (상세)
                top_keys = list(data.keys())[:8] if isinstance(data, dict) else type(data).__name__
                data_val = data.get("data") if isinstance(data, dict) else None
                if isinstance(data_val, dict):
                    data_keys = list(data_val.keys())[:8]
                    print(f"  [PROBE] {label}: HTTP 200, 상품없음 keys={top_keys} data.keys={data_keys}")
                elif isinstance(data_val, list):
                    print(f"  [PROBE] {label}: HTTP 200, 상품없음 keys={top_keys} data=[{len(data_val)}]")
                else:
                    # 상세 구조 출력
                    raw = r.text[:200]
                    print(f"  [PROBE] {label}: HTTP 200, 상품없음 keys={top_keys} raw={raw}")
        except Exception as e:
            print(f"  [PROBE] {label}: 오류={e}")

    print("  [PROBE] 카테고리 전용 API 미발견")
    return ""


def _fetch_via_ranking_api(cat_code: str, cat_label: str) -> list:
    """탐색된 카테고리 API 엔드포인트로 수집"""
    global _MUSINSA_CAT_ENDPOINT
    if not _MUSINSA_CAT_ENDPOINT:
        return []

    parts = _MUSINSA_CAT_ENDPOINT.split("|", 2)
    if len(parts) < 3:
        return []
    _, url, params_template = parts
    params = json.loads(params_template)
    # categoryCode를 현재 카테고리로 교체
    params["categoryCode"] = cat_code

    headers = {
        "User-Agent": BROWSER_UA,
        "Referer": "https://www.musinsa.com/",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ko-KR,ko;q=0.9",
        "Origin": "https://www.musinsa.com",
    }
    resp = requests.get(url, params=params, headers=headers, timeout=20)
    resp.raise_for_status()
    data = resp.json()

    goods_list = _search_product_list(data)
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

def _fetch_via_home_widget(cat_code: str, cat_label: str) -> list:
    """홈 위젯 API — sectionId=200, categoryCode 지원 (카테고리별 필터링)"""
    api_cat = cat_code if cat_code else "000"
    url = "https://client.musinsa.com/api/home/web/v5/pans/ranking"
    params = {
        "storeCode": "musinsa",
        "sectionId": "200",
        "gf": "M",
        "categoryCode": api_cat,
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

    # 방법 3: 홈 위젯 API — 전체 및 카테고리 모두 지원
    try:
        result = _fetch_via_home_widget(cat_code, cat_label)
        if result:
            print(f"    ✓ [홈위젯] {len(result)}개")
            return result
        print(f"    [WARN] 홈위젯 빈 응답")
    except Exception as e:
        print(f"    [WARN] 홈위젯 실패: {e}")

    print(f"    [ERROR] 모든 방법 실패")
    return []


def fetch_musinsa() -> dict:
    global _MUSINSA_CAT_ENDPOINT
    print("▶ 무신사 랭킹 수집 시작...")
    print("  카테고리 API 탐색 중...")
    _MUSINSA_CAT_ENDPOINT = _probe_musinsa_category_endpoint()
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

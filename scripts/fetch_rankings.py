"""
무신사 & 29CM 랭킹 데이터 수집 스크립트
GitHub Actions에서 1시간마다 자동 실행됩니다.

- 무신사: 랭킹/베스트 페이지 크롤링 (requests + BeautifulSoup)
- 29CM  : 랭킹 페이지 크롤링
- 이전 데이터와 비교하여 순위 변동 계산
- data/musinsa.json, data/29cm.json 으로 저장
"""

import json
import time
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ── 경로 설정 ──────────────────────────────────────
ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.musinsa.com/",
}

MUSINSA_CATEGORIES = {
    "": "전체",
    "001": "상의",
    "002": "아우터",
    "003": "바지",
    "004": "원피스/스커트",
    "005": "스포츠",
    "020": "신발",
    "022": "가방",
    "023": "시계/쥬얼리",
    "024": "패션잡화",
    "026": "화장품/향수",
}

CM29_CATEGORIES = {
    "": "전체",
    "tops": "상의",
    "outer": "아우터",
    "bottoms": "하의",
    "dress": "원피스",
    "shoes": "신발",
    "bag": "가방",
    "acc": "액세서리",
    "beauty": "뷰티",
    "life": "라이프",
}


# ═══════════════════════════════════════════════════
#  무신사 크롤러
# ═══════════════════════════════════════════════════

def fetch_musinsa_category(category_code: str, category_label: str, limit: int = 30) -> list:
    """무신사 랭킹 베스트 페이지에서 상품 정보를 파싱합니다."""
    url = (
        "https://www.musinsa.com/ranking/best"
        f"?storeCode=musinsa&period=now&age=ALL"
        f"&category={category_code}&subcategory=&page=1&displayRange=60"
    )
    items = []
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # 무신사 랭킹 리스트 파싱
        # 실제 HTML 구조에 따라 선택자를 조정해야 할 수 있습니다
        product_list = soup.select("li.list-item, .ranking-list li, .goods-list-item")

        if not product_list:
            # 대안 선택자 시도
            product_list = soup.select("[class*='ranking'] li, [class*='goods'] li")

        for i, el in enumerate(product_list[:limit]):
            try:
                # 브랜드명
                brand_el = el.select_one(".brand-name, .item-title span, [class*='brand']")
                brand = brand_el.get_text(strip=True) if brand_el else "Unknown"

                # 상품명
                name_el = el.select_one(".goods-name, .item-name, [class*='goods-name'], [class*='product-name']")
                name = name_el.get_text(strip=True) if name_el else f"상품 #{i+1}"

                # 가격
                price_el = el.select_one(".price, [class*='price']")
                price_text = price_el.get_text(strip=True) if price_el else "0"
                price = int(re.sub(r"[^\d]", "", price_text) or "0")

                # 할인율
                discount_el = el.select_one(".sale-rate, [class*='discount'], [class*='sale']")
                discount_text = discount_el.get_text(strip=True) if discount_el else "0"
                discount_rate = int(re.sub(r"[^\d]", "", discount_text) or "0")
                final_price = int(price * (1 - discount_rate / 100)) if discount_rate else price

                # 상품 링크에서 ID 추출
                link_el = el.select_one("a[href*='/goods/']")
                product_id = ""
                if link_el and link_el.get("href"):
                    match = re.search(r"/goods/(\d+)", link_el["href"])
                    if match:
                        product_id = match.group(1)

                items.append({
                    "rank": i + 1,
                    "brand": brand,
                    "name": name if name else f"{brand} 상품",
                    "price": price,
                    "discountRate": discount_rate,
                    "finalPrice": final_price,
                    "category": category_code,
                    "categoryLabel": category_label,
                    "change": None,  # 이후 비교에서 채움
                    "id": f"ms-{product_id or i+1}",
                })
            except Exception as e:
                print(f"  [WARN] 무신사 상품 파싱 오류 (idx={i}): {e}")
                continue

    except requests.RequestException as e:
        print(f"  [ERROR] 무신사 카테고리 {category_code} 요청 실패: {e}")

    return items


def fetch_musinsa_all() -> dict:
    """모든 카테고리의 무신사 랭킹을 수집합니다."""
    print("▶ 무신사 랭킹 수집 시작...")
    all_items = []
    seen_ids = set()

    # 전체 랭킹 먼저 수집 (50개)
    main_items = fetch_musinsa_category("", "전체", limit=50)
    for item in main_items:
        if item["id"] not in seen_ids:
            all_items.append(item)
            seen_ids.add(item["id"])

    # 카테고리별 수집
    for code, label in list(MUSINSA_CATEGORIES.items())[1:]:
        print(f"  - 카테고리: {label} ({code})")
        cat_items = fetch_musinsa_category(code, label, limit=20)

        for item in cat_items:
            # 이미 전체 랭킹에 있으면 카테고리 정보만 보강, 없으면 추가
            if item["id"] in seen_ids:
                for existing in all_items:
                    if existing["id"] == item["id"] and not existing["category"]:
                        existing["category"] = code
                        existing["categoryLabel"] = label
                        break
            else:
                all_items.append(item)
                seen_ids.add(item["id"])

        time.sleep(1.5)  # 서버 부하 방지

    print(f"  ✓ 무신사 총 {len(all_items)}개 상품 수집")
    return {
        "platform": "musinsa",
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "items": all_items,
    }


# ═══════════════════════════════════════════════════
#  29CM 크롤러
# ═══════════════════════════════════════════════════

def fetch_29cm_ranking(limit: int = 50) -> list:
    """29CM 랭킹 페이지에서 상품 정보를 파싱합니다."""
    url = "https://29cm.co.kr/home/ranking"
    items = []
    headers_29cm = {**HEADERS, "Referer": "https://29cm.co.kr/"}

    try:
        resp = requests.get(url, headers=headers_29cm, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # 29CM은 Next.js 기반이라 __NEXT_DATA__ JSON에서 데이터 추출 시도
        next_data_el = soup.select_one("#__NEXT_DATA__")
        if next_data_el:
            try:
                next_data = json.loads(next_data_el.string)
                # 랭킹 데이터 경로 탐색 (실제 구조에 따라 조정 필요)
                props = next_data.get("props", {}).get("pageProps", {})
                ranking_items = (
                    props.get("rankingItems")
                    or props.get("items")
                    or props.get("products")
                    or []
                )
                for i, prod in enumerate(ranking_items[:limit]):
                    brand = prod.get("brandName") or prod.get("brand", {}).get("nameKr") or "Unknown"
                    name = prod.get("itemName") or prod.get("name") or f"상품 #{i+1}"
                    price = int(prod.get("consumerPrice") or prod.get("price") or 0)
                    discount = int(prod.get("discountRate") or 0)
                    final_price = int(price * (1 - discount / 100)) if discount else price
                    category = prod.get("categoryCode") or prod.get("category", {}).get("code") or ""
                    category_label = CM29_CATEGORIES.get(category, "기타")
                    product_id = str(prod.get("itemNo") or prod.get("id") or i + 1)

                    items.append({
                        "rank": i + 1,
                        "brand": brand,
                        "name": name,
                        "price": price,
                        "discountRate": discount,
                        "finalPrice": final_price,
                        "category": category,
                        "categoryLabel": category_label,
                        "change": None,
                        "id": f"cm-{product_id}",
                    })
                if items:
                    print(f"  ✓ 29CM __NEXT_DATA__ 파싱 성공: {len(items)}개")
                    return items
            except (json.JSONDecodeError, KeyError) as e:
                print(f"  [WARN] 29CM __NEXT_DATA__ 파싱 실패: {e}")

        # Fallback: HTML 직접 파싱
        product_els = soup.select("[class*='RankingItem'], [class*='ranking-item'], [class*='product-item']")
        for i, el in enumerate(product_els[:limit]):
            try:
                brand_el = el.select_one("[class*='brand'], [class*='Brand']")
                brand = brand_el.get_text(strip=True) if brand_el else "Unknown"
                name_el = el.select_one("[class*='name'], [class*='Name'], [class*='title']")
                name = name_el.get_text(strip=True) if name_el else f"상품 #{i+1}"
                price_el = el.select_one("[class*='price'], [class*='Price']")
                price_text = price_el.get_text(strip=True) if price_el else "0"
                price = int(re.sub(r"[^\d]", "", price_text) or "0")

                items.append({
                    "rank": i + 1,
                    "brand": brand,
                    "name": name,
                    "price": price,
                    "discountRate": 0,
                    "finalPrice": price,
                    "category": "",
                    "categoryLabel": "전체",
                    "change": None,
                    "id": f"cm-{i+1}",
                })
            except Exception as e:
                print(f"  [WARN] 29CM HTML 파싱 오류 (idx={i}): {e}")

    except requests.RequestException as e:
        print(f"  [ERROR] 29CM 요청 실패: {e}")

    return items


def fetch_29cm_all() -> dict:
    """29CM 전체 랭킹을 수집합니다."""
    print("▶ 29CM 랭킹 수집 시작...")
    items = fetch_29cm_ranking(limit=100)
    print(f"  ✓ 29CM 총 {len(items)}개 상품 수집")
    return {
        "platform": "29cm",
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "items": items,
    }


# ═══════════════════════════════════════════════════
#  순위 변동 계산
# ═══════════════════════════════════════════════════

def compute_rank_changes(new_items: list, old_items: list) -> list:
    """이전 데이터와 비교해서 순위 변동(change 필드)을 채웁니다."""
    old_rank_map = {item["id"]: item["rank"] for item in old_items}

    for item in new_items:
        old_rank = old_rank_map.get(item["id"])
        if old_rank is None:
            item["change"] = "NEW"
        else:
            delta = old_rank - item["rank"]  # 양수 = 상승, 음수 = 하락
            item["change"] = delta if delta != 0 else 0

    return new_items


# ═══════════════════════════════════════════════════
#  파일 저장
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
    musinsa_data = fetch_musinsa_all()
    musinsa_data["items"] = compute_rank_changes(musinsa_data["items"], old_musinsa)
    save("musinsa", musinsa_data)

    time.sleep(2)

    # 29CM
    old_29cm = load_existing("29cm")
    data_29cm = fetch_29cm_all()
    data_29cm["items"] = compute_rank_changes(data_29cm["items"], old_29cm)
    save("29cm", data_29cm)

    print(f"\n{'='*50}")
    print(f"  ✅ 완료: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    main()

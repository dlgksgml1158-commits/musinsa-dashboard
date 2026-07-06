"""무신사 파트너센터 프로모션 일정(일반 공개 정보만) 수집.

셀러 계정별 참여 현황(참여상품수, 승인/반려 등)은 비공개 정보이므로
절대 포함하지 않는다 — 프로모션명/기간/상태/최소할인율/모집마감일만 저장한다.
"""
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

API_URL = "https://itgg-api.musinsa.com/po/sale/promotions"
OUT_PATH = "data/musinsa_promotions.json"


def fetch_promotions(cookie):
    now = datetime.now(timezone.utc)
    params = {
        "rangeFrom": now.isoformat(),
        "rangeTo": (now + timedelta(days=60)).isoformat(),
        "page": "1",
        "limit": "100",
    }
    url = f"{API_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0",
            "Cookie": cookie,
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)


def extract_items(payload):
    items = []
    for p in payload.get("data", {}).get("promotions", []):
        items.append({
            "promotionId": p.get("promotionId"),
            "name": p.get("promotionName", ""),
            "status": p.get("status", ""),
            "from": p.get("rangeFrom", ""),
            "to": p.get("rangeTo", ""),
            "minPercent": p.get("minPercent"),
            "recruitDeadline": p.get("recruitDeadline", ""),
        })
    return items


def main():
    cookie = os.environ.get("MUSINSA_PARTNER_COOKIE", "")
    items = []
    if cookie:
        try:
            payload = fetch_promotions(cookie)
            items = extract_items(payload)
        except Exception as e:
            print(f"Failed to fetch partner promotions: {e}")
    else:
        print("MUSINSA_PARTNER_COOKIE not set, skipping")

    output = {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "items": items,
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"Saved {len(items)} promotion items to {OUT_PATH}")


if __name__ == "__main__":
    main()

"""무신사 파트너 상품별 판매 순위(베스트셀러) 동기화."""
import json
import os
import urllib.request
from datetime import datetime, timezone

SOURCE_URL = (
    "https://raw.githubusercontent.com/dlgksgml1158-commits/"
    "notion-github-dashboard/main/data-b53e82ab173f/musinsa_bestsellers.json"
)
OUT_PATH = "data/musinsa_bestsellers.json"


def main():
    items = []
    by_date = {}
    start_date = ""
    end_date = ""
    try:
        req = urllib.request.Request(SOURCE_URL, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.load(resp)
        items = data.get("items", [])
        by_date = data.get("byDate", {})
        start_date = data.get("startDate", "")
        end_date = data.get("endDate", "")
    except Exception as e:
        print(f"Failed to fetch bestsellers: {e}")

    # 상위 저장소 쪽 스크래핑이 일시적으로 실패해 빈 데이터를 내려주면,
    # 화면이 "데이터 없음"으로 빠지지 않도록 기존 데이터를 그대로 유지한다.
    if not items and os.path.exists(OUT_PATH):
        try:
            with open(OUT_PATH, encoding="utf-8") as f:
                prev = json.load(f)
            if prev.get("items"):
                print("Fetched 0 items; keeping previous bestsellers")
                return
        except Exception:
            pass

    output = {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "startDate": start_date,
        "endDate": end_date,
        "items": items,
        "byDate": by_date,
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"Saved {len(items)} bestseller items + {len(by_date)} days to {OUT_PATH}")


if __name__ == "__main__":
    main()

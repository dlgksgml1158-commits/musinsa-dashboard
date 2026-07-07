"""무신사 파트너센터 프로모션 일정 동기화.

notion-github-dashboard 저장소가 Playwright 로그인으로 직접 수집한
프로모션 일정(계정별 참여정보 제외, 이미 정제된 공개 데이터)을 그대로
가져와 표시한다. 이 저장소에서는 로그인 인증을 하지 않는다.
"""
import json
import urllib.request
from datetime import datetime, timezone

SOURCE_URL = (
    "https://raw.githubusercontent.com/dlgksgml1158-commits/"
    "notion-github-dashboard/main/data-b53e82ab173f/musinsa_partner_promotions.json"
)
OUT_PATH = "data/musinsa_promotions.json"


def main():
    items = []
    try:
        req = urllib.request.Request(SOURCE_URL, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.load(resp)
        items = data.get("items", [])
    except Exception as e:
        print(f"Failed to fetch partner promotions: {e}")

    output = {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "items": items,
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"Saved {len(items)} promotion items to {OUT_PATH}")


if __name__ == "__main__":
    main()

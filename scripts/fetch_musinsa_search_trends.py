"""무신사 실시간 인기/급상승 검색어 수집. 로그인 불필요한 공개 API."""
import json
import urllib.request
from datetime import datetime, timezone

API_URL = "https://client.musinsa.com/api/display/v1/search/web/keyword/search-home"
OUT_PATH = "data/musinsa_search_trends.json"


def fetch_trends():
    req = urllib.request.Request(
        API_URL,
        headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)


def extract(payload):
    result = {"popular": [], "rising": []}
    for comp in payload.get("data", {}).get("componentList", []):
        key = comp.get("key")
        if key not in result:
            continue
        for i, item in enumerate(comp.get("items", []), start=1):
            result[key].append({
                "rank": i,
                "keyword": item.get("text", ""),
                "rankIncrement": item.get("rankIncrement", 0),
            })
    return result


def main():
    result = {"popular": [], "rising": []}
    try:
        payload = fetch_trends()
        result = extract(payload)
    except Exception as e:
        print(f"Failed to fetch search trends: {e}")

    output = {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        **result,
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"Saved {len(result['popular'])} popular / {len(result['rising'])} rising keywords to {OUT_PATH}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
import json, time, os, requests
from datetime import datetime

API = "https://www.dhlottery.co.kr/common.do?method=getLottoNumber&drwNo={}"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://www.dhlottery.co.kr/",
    "Accept": "application/json, text/javascript, */*",
    "X-Requested-With": "XMLHttpRequest",
}

def fetch_one(n):
    try:
        r = requests.get(API.format(n), headers=HEADERS, timeout=10)
        d = r.json()
        if d.get("returnValue") == "success":
            return {
                "round":   int(d["drwNo"]),
                "date":    str(d["drwNoDate"]),
                "numbers": [int(d[f"drwtNo{i}"]) for i in range(1,7)],
                "bonus":   int(d["bnusNo"]),
                "w1": int(d.get("firstPrzwnerCo") or 0),
                "a1": int(d.get("firstWinamnt") or 0),
                "w2": int(d.get("secondPrzwnerCo") or 0),
                "a2": int(d.get("secondWinamnt") or 0),
                "w3": int(d.get("thirdPrzwnerCo") or 0),
                "a3": int(d.get("thirdWinamnt") or 0),
            }
    except Exception as e:
        print(f"  {n}회 실패: {e}")
    return None

def find_latest():
    # 1230부터 1회씩 내려오며 탐색
    for n in range(1230, 1150, -1):
        try:
            r = requests.get(API.format(n), headers=HEADERS, timeout=8)
            d = r.json()
            if d.get("returnValue") == "success":
                print(f"최신 회차: {n}회 ({d['drwNoDate']})")
                return n
        except:
            pass
        time.sleep(0.1)
    return 1200

def main():
    print("로또 데이터 수집 시작")

    # 기존 데이터 로드
    existing = {}
    if os.path.exists("lotto_data.json"):
        try:
            with open("lotto_data.json", "r", encoding="utf-8") as f:
                old = json.load(f)
            existing = {d["round"]: d for d in old.get("draws", [])}
            print(f"기존: {len(existing)}회차")
        except:
            pass

    latest = find_latest()

    # 최근 50회차만 수집 (없는 것만)
    target = list(range(latest, max(1, latest-50), -1))
    to_fetch = [n for n in target if n not in existing]
    print(f"신규 수집: {len(to_fetch)}회차")

    for n in to_fetch:
        d = fetch_one(n)
        if d:
            existing[n] = d
            print(f"  ✓ {n}회 {d['date']} {d['numbers']}")
        time.sleep(0.15)

    # 저장 (최근 500회차만 유지)
    draws = sorted(existing.values(), key=lambda x: x["round"], reverse=True)[:500]

    if not draws:
        print("❌ 수집 실패")
        raise SystemExit(1)

    with open("lotto_data.json", "w", encoding="utf-8") as f:
        json.dump({
            "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "latest_round": draws[0]["round"],
            "total": len(draws),
            "draws": draws,
        }, f, ensure_ascii=False, indent=2)

    print(f"✅ 완료: {len(draws)}회차 저장")
    print(f"   최신: {draws[0]['round']}회 {draws[0]['numbers']}")

if __name__ == "__main__":
    main()

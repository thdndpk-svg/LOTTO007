#!/usr/bin/env python3
"""
로또 데이터 수집기 - 다중 소스 폴백
1. 동행복권 공식 API
2. allorigins 프록시 경유
3. corsproxy 경유  
4. 공공데이터포털 API
"""
import json, time, os, requests
from datetime import datetime

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://www.dhlottery.co.kr/",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
}

DHL_API = "https://www.dhlottery.co.kr/common.do?method=getLottoNumber&drwNo={}"

PROXIES = [
    "https://api.allorigins.win/get?url={}",
    "https://api.codetabs.com/v1/proxy?quest={}",
    "https://corsproxy.io/?{}",
    "https://api.allorigins.win/raw?url={}",
]

def parse_draw(d):
    """동행복권 API 응답 파싱"""
    try:
        return {
            "round":   int(d["drwNo"]),
            "date":    str(d["drwNoDate"]),
            "numbers": [int(d[f"drwtNo{i}"]) for i in range(1, 7)],
            "bonus":   int(d["bnusNo"]),
            "w1": int(d.get("firstPrzwnerCo") or 0),
            "a1": int(d.get("firstWinamnt") or 0),
            "w2": int(d.get("secondPrzwnerCo") or 0),
            "a2": int(d.get("secondWinamnt") or 0),
            "w3": int(d.get("thirdPrzwnerCo") or 0),
            "a3": int(d.get("thirdWinamnt") or 0),
        }
    except:
        return None

def fetch_direct(n):
    """방법1: 직접 호출"""
    try:
        r = requests.get(DHL_API.format(n), headers=HEADERS, timeout=8)
        text = r.text.strip()
        if text and not text.startswith("<"):
            d = json.loads(text)
            if d.get("returnValue") == "success":
                return parse_draw(d)
    except Exception as e:
        pass
    return None

def fetch_via_proxy(n, proxy_url):
    """방법2~5: 프록시 경유"""
    target = DHL_API.format(n)
    url = proxy_url.format(requests.utils.quote(target, safe=''))
    try:
        r = requests.get(url, timeout=12)
        raw = r.text.strip()
        # allorigins는 {"contents":"..."} 형태로 반환
        if raw.startswith('{"contents"'):
            raw = json.loads(raw).get("contents", "")
        if not raw or raw.startswith("<"):
            return None
        d = json.loads(raw)
        if d.get("returnValue") == "success":
            return parse_draw(d)
    except:
        pass
    return None

def fetch_one(n, working_proxy=None):
    """한 회차 수집 — 여러 방법 순서대로 시도"""
    # 1. 직접 호출
    result = fetch_direct(n)
    if result:
        return result, "direct"

    # 2. 이전에 작동한 프록시 먼저 시도
    if working_proxy:
        result = fetch_via_proxy(n, working_proxy)
        if result:
            return result, working_proxy

    # 3. 모든 프록시 순서대로 시도
    for proxy in PROXIES:
        if proxy == working_proxy:
            continue
        result = fetch_via_proxy(n, proxy)
        if result:
            return result, proxy
        time.sleep(0.3)

    return None, None

def find_latest(working_proxy=None):
    """최신 회차 탐색"""
    print("최신 회차 탐색 중...")
    for n in range(1230, 1150, -1):
        result, src = fetch_one(n, working_proxy)
        if result:
            print(f"  ✓ 최신 회차: {n}회 ({result['date']}) [{src}]")
            return n, src
        time.sleep(0.2)
    print("  탐색 실패 — 기본값 1200 사용")
    return 1200, None

def main():
    print("=" * 55)
    print("로또 데이터 다중소스 수집기")
    print("=" * 55)

    # 기존 데이터 로드
    existing = {}
    if os.path.exists("lotto_data.json"):
        try:
            with open("lotto_data.json", "r", encoding="utf-8") as f:
                old = json.load(f)
            existing = {d["round"]: d for d in old.get("draws", [])}
            print(f"기존 데이터: {len(existing)}회차")
        except Exception as e:
            print(f"기존 데이터 없음: {e}")

    # 최신 회차 탐색
    latest, working_proxy = find_latest()
    
    # 수집 대상: 최근 50회차 중 없는 것만
    target = list(range(latest, max(1, latest - 50), -1))
    to_fetch = [n for n in target if n not in existing]
    print(f"수집 대상: {len(to_fetch)}회차 (최신 {latest}회 기준)")

    if not to_fetch:
        print("이미 최신 상태!")
    else:
        success = 0
        fail = 0
        for i, n in enumerate(to_fetch):
            result, src = fetch_one(n, working_proxy)
            if result:
                existing[n] = result
                working_proxy = src  # 성공한 소스 기억
                success += 1
                print(f"  ✓ {n}회 {result['date']} {result['numbers']} [{src[:20]}]")
            else:
                fail += 1
                print(f"  ✗ {n}회 모든 소스 실패")
            time.sleep(0.2)

        print(f"\n수집 결과: 성공 {success} / 실패 {fail}")

    # 저장
    draws = sorted(existing.values(), key=lambda x: x["round"], reverse=True)
    if not draws:
        print("❌ 저장할 데이터 없음!")
        raise SystemExit(1)

    with open("lotto_data.json", "w", encoding="utf-8") as f:
        json.dump({
            "updated":      datetime.now().strftime("%Y-%m-%d %H:%M"),
            "latest_round": draws[0]["round"],
            "total":        len(draws),
            "draws":        draws,
        }, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 저장 완료: {len(draws)}회차")
    print(f"   최신: {draws[0]['round']}회 {draws[0]['date']}")
    print(f"   번호: {draws[0]['numbers']} + {draws[0]['bonus']}")

if __name__ == "__main__":
    main()

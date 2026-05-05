#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
로또 데이터 자동 수집기 - 안전 검증형

핵심:
- 회차별 개별 페이지 우선 사용
- 동행복권 공식 JSON 보조 사용
- 잘못 파싱된 반복 번호 자동 차단
- 기존 lotto_data.json이 깨졌어도 clean rebuild 가능
"""

import html
import json
import os
import re
import time
import urllib.request
from datetime import datetime, date, timezone, timedelta
from pathlib import Path

DATA_PATH = Path("lotto_data.json")

KST = timezone(timedelta(hours=9))
FIRST_DRAW_DATE = date(2002, 12, 7)

LOTTO_HISTORY_LIMIT = int(os.getenv("LOTTO_HISTORY_LIMIT", "120"))
PAGE_TIMEOUT = float(os.getenv("PAGE_TIMEOUT", "10"))
DHL_TIMEOUT = float(os.getenv("DHL_TIMEOUT", "4"))
CLEAN_REBUILD = os.getenv("LOTTO_CLEAN_REBUILD", "1") == "1"

SOURCE_ORDER = [
    s.strip()
    for s in os.getenv("LOTTO_SOURCE_ORDER", "lottotapa_single,dhlottery").split(",")
    if s.strip()
]

HEADERS_HTML = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}

HEADERS_JSON = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    "Referer": "https://www.dhlottery.co.kr/gameResult.do?method=byWin",
    "X-Requested-With": "XMLHttpRequest",
}

def log(msg):
    print(str(msg), flush=True)

def fetch_text(url, headers, timeout):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return res.read().decode("utf-8", errors="replace")

def estimated_latest_round():
    today = datetime.now(KST).date()
    weeks = (today - FIRST_DRAW_DATE).days // 7
    return max(1, weeks + 1)

def strip_html(raw):
    raw = re.sub(r"(?is)<script.*?</script>", " ", raw)
    raw = re.sub(r"(?is)<style.*?</style>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>", "\n", raw)
    raw = re.sub(r"(?i)</(div|p|li|tr|td|h1|h2|h3|h4|h5|h6|span|strong|em)>", "\n", raw)
    raw = re.sub(r"(?is)<[^>]+>", "\n", raw)
    raw = html.unescape(raw)

    lines = []
    for line in raw.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            lines.append(line)

    return "\n".join(lines)

def is_valid_draw(draw):
    if not isinstance(draw, dict):
        return False

    try:
        round_no = int(draw["round"])
        numbers = [int(x) for x in draw["numbers"]]
        bonus = int(draw["bonus"])
    except Exception:
        return False

    if round_no < 1:
        return False

    if len(numbers) != 6:
        return False

    if len(set(numbers)) != 6:
        return False

    if not all(1 <= n <= 45 for n in numbers):
        return False

    if not (1 <= bonus <= 45):
        return False

    if bonus in numbers:
        return False

    if not re.match(r"^\d{4}-\d{2}-\d{2}$", str(draw.get("date", ""))):
        return False

    return True

def normalize_draw(draw, source):
    draw = dict(draw)
    draw["round"] = int(draw["round"])
    draw["date"] = str(draw["date"])
    draw["numbers"] = [int(x) for x in draw["numbers"]]
    draw["bonus"] = int(draw["bonus"])
    draw["source"] = source

    for key in ["w1", "a1", "w2", "a2", "w3", "a3"]:
        try:
            draw[key] = int(draw.get(key) or 0)
        except Exception:
            draw[key] = 0

    return draw

def parse_dhlottery_json(text, round_no):
    text = (text or "").strip()

    if not text or text.startswith("<"):
        return None

    try:
        d = json.loads(text)
    except Exception:
        return None

    if d.get("returnValue") != "success":
        return None

    try:
        draw = {
            "round": int(d["drwNo"]),
            "date": str(d["drwNoDate"]),
            "numbers": [int(d[f"drwtNo{i}"]) for i in range(1, 7)],
            "bonus": int(d["bnusNo"]),
            "w1": int(d.get("firstPrzwnerCo") or 0),
            "a1": int(d.get("firstWinamnt") or 0),
            "w2": int(d.get("secondPrzwnerCo") or 0),
            "a2": int(d.get("secondWinamnt") or 0),
            "w3": int(d.get("thirdPrzwnerCo") or 0),
            "a3": int(d.get("thirdWinamnt") or 0),
        }

        if int(draw["round"]) != int(round_no):
            return None

        return draw if is_valid_draw(draw) else None
    except Exception:
        return None

def fetch_dhlottery(round_no):
    url = f"https://www.dhlottery.co.kr/common.do?method=getLottoNumber&drwNo={round_no}"

    try:
        text = fetch_text(url, HEADERS_JSON, DHL_TIMEOUT)
        draw = parse_dhlottery_json(text, round_no)

        if draw:
            return normalize_draw(draw, "dhlottery")
    except Exception as e:
        log(f"  dhlottery 실패 {round_no}회: {e}")

    return None

def parse_lottotapa_single(raw, round_no):
    text = strip_html(raw)

    # 개별 회차 페이지에서만 정확히 파싱
    title_patterns = [
        rf"{round_no}회\s*로또\s*당첨번호\s*\((\d{{4}}-\d{{2}}-\d{{2}})\)",
        rf"{round_no}회\s*동행복권\s*로또\s*당첨번호\s*\(\s*추첨일자\s*:\s*(\d{{4}}-\d{{2}}-\d{{2}})\s*\)",
    ]

    match = None
    for pattern in title_patterns:
        match = re.search(pattern, text, re.S)
        if match:
            break

    if not match:
        return None

    date_str = match.group(1)

    # 제목 근처만 사용. 너무 넓게 잡으면 다른 회차 번호가 섞임.
    chunk = text[match.end(): match.end() + 900]

    # 추첨기 번호 제거
    chunk = re.sub(r"\b\d+\s*호기\b", " ", chunk)

    nums = [
        int(x)
        for x in re.findall(r"(?<!\d)([1-9]|[1-3]\d|4[0-5])(?!\d)", chunk)
    ]

    if len(nums) < 7:
        return None

    draw = {
        "round": int(round_no),
        "date": date_str,
        "numbers": nums[:6],
        "bonus": nums[6],
        "w1": 0,
        "a1": 0,
        "w2": 0,
        "a2": 0,
        "w3": 0,
        "a3": 0,
    }

    return draw if is_valid_draw(draw) else None

def fetch_lottotapa_single(round_no):
    url = f"https://lottotapa.com/stat/result/{round_no}"

    try:
        raw = fetch_text(url, HEADERS_HTML, PAGE_TIMEOUT)
        draw = parse_lottotapa_single(raw, round_no)

        if draw:
            return normalize_draw(draw, "lottotapa_single")
    except Exception as e:
        log(f"  lottotapa_single 실패 {round_no}회: {e}")

    return None

SOURCE_FUNCS = {
    "lottotapa_single": fetch_lottotapa_single,
    "dhlottery": fetch_dhlottery,
}

def fetch_round(round_no):
    for source in SOURCE_ORDER:
        func = SOURCE_FUNCS.get(source)

        if not func:
            continue

        draw = func(round_no)

        if draw and is_valid_draw(draw):
            log(f"  {source} 성공 {round_no}회")
            return draw

    return None

def load_existing():
    if not DATA_PATH.exists():
        log("기존 lotto_data.json 없음.")
        return {}

    try:
        data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
        existing = {}

        for item in data.get("draws", []):
            if "round" in item and is_valid_draw(item):
                existing[int(item["round"])] = item

        log(f"기존 데이터 로드: {len(existing)}회차")
        return existing
    except Exception as e:
        log(f"기존 파일 읽기 실패: {e}")
        return {}

def find_latest():
    log("최신 회차 탐색 시작")

    estimate = estimated_latest_round()
    log(f"예상 최신 회차: {estimate}")

    for round_no in range(estimate + 3, max(1, estimate - 25), -1):
        log(f"확인 중: {round_no}회")

        draw = fetch_round(round_no)

        if draw:
            log(
                f"최신 회차 발견: {draw['round']}회 "
                f"{draw['date']} {draw['numbers']} + {draw['bonus']} "
                f"source={draw.get('source')}"
            )
            return int(draw["round"]), draw

        time.sleep(0.1)

    raise RuntimeError("최신 회차를 찾지 못했습니다.")

def validate_no_bad_repeats(draws):
    """
    같은 번호+보너스가 여러 회차 연속 반복되면 파싱 오류로 판단.
    실제로 같은 조합이 연속으로 여러 번 나올 가능성은 사실상 없으므로 자동 차단.
    """
    ordered = sorted(draws, key=lambda x: int(x["round"]), reverse=True)

    streak = 1
    prev_key = None

    for draw in ordered:
        key = (tuple(draw["numbers"]), int(draw["bonus"]))

        if key == prev_key:
            streak += 1
        else:
            streak = 1
            prev_key = key

        if streak >= 3:
            raise RuntimeError(
                f"반복 번호 감지: {streak}회 연속 {list(key[0])} + {key[1]} / 파싱 오류 가능성"
            )

def save_data(draw_map):
    draws = sorted(draw_map.values(), key=lambda x: int(x["round"]), reverse=True)

    if not draws:
        raise RuntimeError("저장할 데이터가 없습니다.")

    validate_no_bad_repeats(draws)

    payload = {
        "status": "ok",
        "updated": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
        "latest_round": int(draws[0]["round"]),
        "total": len(draws),
        "sources": sorted(set(str(d.get("source", "unknown")) for d in draws)),
        "draws": draws,
    }

    temp_path = DATA_PATH.with_suffix(".json.tmp")
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp_path.replace(DATA_PATH)

    log("저장 완료")
    log(f"latest_round: {payload['latest_round']}")
    log(f"total: {payload['total']}")
    log(f"sources: {payload['sources']}")

def main():
    log("=" * 60)
    log("로또 데이터 안전 업데이트 시작")
    log("=" * 60)

    existing = load_existing()

    latest_round, latest_draw = find_latest()

    start = latest_round
    end = max(1, latest_round - LOTTO_HISTORY_LIMIT + 1)

    log(f"수집 범위: {start}회 ~ {end}회")
    log(f"소스 순서: {SOURCE_ORDER}")
    log(f"클린 재생성: {CLEAN_REBUILD}")

    fresh = {}
    success = 0
    fail = 0
    cached = 0

    for round_no in range(start, end - 1, -1):
        log(f"수집 중: {round_no}회")

        draw = fetch_round(round_no)

        if draw:
            fresh[round_no] = draw
            success += 1
            log(f"  저장 {round_no}회 {draw['numbers']} + {draw['bonus']} source={draw.get('source')}")
        else:
            fail += 1

            if not CLEAN_REBUILD and round_no in existing:
                fresh[round_no] = existing[round_no]
                cached += 1
                log(f"  기존 캐시 유지 {round_no}회")
            else:
                log(f"  전체 소스 실패 {round_no}회")

        time.sleep(0.1)

    min_required = min(30, LOTTO_HISTORY_LIMIT)

    if len(fresh) < min_required:
        raise RuntimeError(f"수집 데이터 부족: {len(fresh)}개 / 최소 {min_required}개 필요")

    save_data(fresh)

    log("=" * 60)
    log(f"완료 성공: {success}, 실패: {fail}, 캐시유지: {cached}")
    log("=" * 60)

if __name__ == "__main__":
    main()

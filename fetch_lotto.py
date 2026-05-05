#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
로또 데이터 자동 수집기 - 멀티 소스 안정형

우선순위:
1) lottotapa_all      : 로또타파 전체 회차 페이지
2) dhlottery          : 동행복권 공식 JSON
3) lottotapa_single   : 로또타파 개별 회차 페이지
4) existing cache     : 기존 lotto_data.json 유지

목표:
- 한 서버가 막혀도 다음 서버로 자동 전환
- GitHub Actions에서 timeout이 나도 앱 데이터는 최대한 유지
- lotto_data.json 자동 생성/갱신
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
DHL_TIMEOUT = float(os.getenv("DHL_TIMEOUT", "3"))
PAGE_TIMEOUT = float(os.getenv("PAGE_TIMEOUT", "10"))

SOURCE_ORDER = os.getenv(
    "LOTTO_SOURCE_ORDER",
    "lottotapa_all,dhlottery,lottotapa_single",
).split(",")

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

LOTTOTAPA_ALL_CACHE = {
    "tried": False,
    "text": None,
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

def normalize_draw(draw, source_name):
    draw = dict(draw)
    draw["round"] = int(draw["round"])
    draw["numbers"] = [int(x) for x in draw["numbers"]]
    draw["bonus"] = int(draw["bonus"])
    draw["date"] = str(draw["date"])
    draw["source"] = source_name

    for key in ["w1", "a1", "w2", "a2", "w3", "a3"]:
        try:
            draw[key] = int(draw.get(key) or 0)
        except Exception:
            draw[key] = 0

    return draw

def merge_draw(old, new):
    """
    새 데이터가 prize 값을 못 가져와 0인 경우,
    기존 데이터의 prize 값이 있으면 보존.
    """
    if not old:
        return new

    merged = dict(old)
    merged.update(new)

    for key in ["w1", "a1", "w2", "a2", "w3", "a3"]:
        if int(new.get(key) or 0) == 0 and int(old.get(key) or 0) > 0:
            merged[key] = old[key]

    return merged

# ------------------------------------------------------------
# 1) 동행복권 공식 JSON
# ------------------------------------------------------------

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

# ------------------------------------------------------------
# 2) 로또타파 HTML 파싱
# ------------------------------------------------------------

def strip_html(raw):
    raw = re.sub(r"(?is)<script.*?</script>", " ", raw)
    raw = re.sub(r"(?is)<style.*?</style>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>", "\n", raw)
    raw = re.sub(r"(?i)</(div|p|li|tr|td|h1|h2|h3|h4|h5|h6|span)>", "\n", raw)
    raw = re.sub(r"(?is)<[^>]+>", "\n", raw)
    raw = html.unescape(raw)

    lines = []
    for line in raw.splitlines():
        line = line.strip()
        if line:
            lines.append(line)

    return "\n".join(lines)

def parse_lottotapa_html(raw, round_no):
    text = strip_html(raw)

    patterns = [
        rf"{round_no}회\s*로또\s*당첨번호\s*\((\d{{4}}-\d{{2}}-\d{{2}})\)",
        rf"{round_no}회\s*동행복권\s*로또\s*당첨번호\s*\(\s*추첨일자\s*:\s*(\d{{4}}-\d{{2}}-\d{{2}})\s*\)",
        rf"{round_no}회.*?(\d{{4}}-\d{{2}}-\d{{2}})",
    ]

    match = None
    for pattern in patterns:
        match = re.search(pattern, text, re.S)
        if match:
            break

    if not match:
        return None

    date_str = match.group(1)

    # 날짜 이후 가까운 영역에서 번호 7개 추출
    chunk = text[match.end(): match.end() + 1800]

    # "2호기" 같은 추첨기 번호 제거
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

def get_lottotapa_all_page():
    if LOTTOTAPA_ALL_CACHE["tried"]:
        return LOTTOTAPA_ALL_CACHE["text"]

    LOTTOTAPA_ALL_CACHE["tried"] = True

    url = "https://lottotapa.com/stat/result_all.php"

    try:
        log("로또타파 전체 회차 페이지 로드")
        text = fetch_text(url, HEADERS_HTML, PAGE_TIMEOUT)
        LOTTOTAPA_ALL_CACHE["text"] = text
        log("로또타파 전체 회차 페이지 로드 성공")
        return text
    except Exception as e:
        log(f"로또타파 전체 페이지 실패: {e}")
        LOTTOTAPA_ALL_CACHE["text"] = None
        return None

def fetch_lottotapa_all(round_no):
    raw = get_lottotapa_all_page()

    if not raw:
        return None

    try:
        draw = parse_lottotapa_html(raw, round_no)

        if draw:
            return normalize_draw(draw, "lottotapa_all")
    except Exception as e:
        log(f"  lottotapa_all 파싱 실패 {round_no}회: {e}")

    return None

def fetch_lottotapa_single(round_no):
    url = f"https://lottotapa.com/stat/result/{round_no}"

    try:
        raw = fetch_text(url, HEADERS_HTML, PAGE_TIMEOUT)
        draw = parse_lottotapa_html(raw, round_no)

        if draw:
            return normalize_draw(draw, "lottotapa_single")
    except Exception as e:
        log(f"  lottotapa_single 실패 {round_no}회: {e}")

    return None

# ------------------------------------------------------------
# 멀티 소스 선택
# ------------------------------------------------------------

SOURCE_FUNCS = {
    "lottotapa_all": fetch_lottotapa_all,
    "dhlottery": fetch_dhlottery,
    "lottotapa_single": fetch_lottotapa_single,
}

def fetch_round(round_no):
    for source_name in SOURCE_ORDER:
        source_name = source_name.strip()

        if source_name not in SOURCE_FUNCS:
            continue

        draw = SOURCE_FUNCS[source_name](round_no)

        if draw and is_valid_draw(draw):
            log(f"  {source_name} 성공 {round_no}회")
            return draw

    return None

def find_latest(existing):
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

    if existing:
        latest_round = max(existing.keys())
        log(f"새 데이터 소스 전부 실패. 기존 캐시 최신 회차 사용: {latest_round}회")
        return latest_round, existing[latest_round]

    raise RuntimeError("최신 회차를 찾지 못했습니다. 기존 캐시도 없습니다.")

# ------------------------------------------------------------
# 파일 로드/저장
# ------------------------------------------------------------

def load_existing():
    if not DATA_PATH.exists():
        log("기존 lotto_data.json 없음. 새로 생성합니다.")
        return {}

    try:
        data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
        draws = data.get("draws", [])
        existing = {}

        for item in draws:
            if "round" in item:
                existing[int(item["round"])] = item

        log(f"기존 데이터: {len(existing)}회차")
        return existing
    except Exception as e:
        log(f"기존 파일 읽기 실패: {e}")
        return {}

def save_data(existing):
    draws = sorted(existing.values(), key=lambda x: int(x["round"]), reverse=True)

    if not draws:
        raise RuntimeError("저장할 데이터가 없습니다.")

    sources = sorted(set(str(d.get("source", "unknown")) for d in draws))

    payload = {
        "status": "ok",
        "updated": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
        "latest_round": int(draws[0]["round"]),
        "total": len(draws),
        "sources": sources,
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
    log("로또 데이터 멀티 소스 업데이트 시작")
    log("=" * 60)

    existing = load_existing()

    latest_round, latest_draw = find_latest(existing)
    existing[latest_round] = merge_draw(existing.get(latest_round), latest_draw)

    start = latest_round
    end = max(1, latest_round - LOTTO_HISTORY_LIMIT + 1)

    log(f"수집 범위: {start}회 ~ {end}회")
    log(f"소스 순서: {SOURCE_ORDER}")

    success = 0
    fail = 0
    skipped = 0

    for round_no in range(start, end - 1, -1):
        # 기존 데이터가 있고 최근 3회가 아니면 재수집 생략
        if round_no in existing and round_no < latest_round - 2:
            skipped += 1
            continue

        log(f"수집 중: {round_no}회")

        draw = fetch_round(round_no)

        if draw:
            existing[round_no] = merge_draw(existing.get(round_no), draw)
            success += 1
            log(f"  저장 {round_no}회 {draw['numbers']} + {draw['bonus']} source={draw.get('source')}")
        else:
            fail += 1
            log(f"  전체 소스 실패 {round_no}회")

        time.sleep(0.1)

    save_data(existing)

    log("=" * 60)
    log(f"완료 성공: {success}, 실패: {fail}, 기존유지: {skipped}")
    log("=" * 60)

if __name__ == "__main__":
    main()

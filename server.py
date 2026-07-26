# -*- coding: utf-8 -*-
"""
제주 오름 추천 MCP 서버 — 0단계 골격.

tool 1개(recommend_oreum) 노출: 사용자 조건으로 오름.db를 필터·랭킹하고,
후보별로 KASI(천문)/KMA(단기예보) 값을 붙여 JSON으로 반환한다.

계산(난이도 상한 결정, 목표시각 스냅, 랭킹)은 전부 이 파일이 결정론적으로 수행하며
LLM에 위임하지 않는다. 안전 특보 연동, 왕복시간 컷오프 게이트, 사용자 좌표 기반
실거리 계산, 가중치 랭킹은 다음 단계 TODO로 남겨둔다(코드 내 주석 참고).

KASI/KMA 호출 로직은 test/test_kasi.py, test/Test_combined.py 에서 검증된 것을
그대로 이식했다.
"""
import contextlib
import math
import os
import sqlite3
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

BASE = Path(__file__).resolve().parent
DB_PATH = BASE / "오름.db"

# Claude Desktop 등 launcher가 어떤 cwd로 프로세스를 띄우든 .env를 찾도록 경로를 고정한다.
load_dotenv(dotenv_path=BASE / ".env")

KMA_AUTHKEY = os.environ.get("KMA_AUTHKEY")
KASI_SERVICE_KEY = os.environ.get("KASI_SERVICE_KEY")

KASI_URL = "http://apis.data.go.kr/B090041/openapi/service/RiseSetInfoService/getLCRiseSetInfo"
KMA_URL = "https://apihub.kma.go.kr/api/typ02/openApi/VilageFcstInfoService_2.0/getVilageFcst"
KASI_TIMEOUT = 15
KMA_TIMEOUT = 15

TABLE, COL_NAME, COL_ADDR = "오름", "오름명", "지번주소"
COL_LAT, COL_LON = "위도", "경도"
COL_NX, COL_NY = "grid_nx", "grid_ny"

SLOTS = [2, 5, 8, 11, 14, 17, 20, 23]
SKY = {"1": "맑음", "3": "구름많음", "4": "흐림"}
PTY = {"0": "없음", "1": "비", "2": "비/눈", "3": "눈", "4": "소나기"}
SKY_RANK_ORDER = {"1": 0, "3": 1, "4": 2}

# child/elderly -> 난이도 상한, solo/None(또는 미지 값) -> None(무제한)
COMPANION_MAX_DIFFICULTY = {"child": 2, "elderly": 2, "solo": None}

# location/난이도 필터가 헐거워 후보가 많이 남을 때, 전부에 대해 순차로 KASI+KMA를
# 호출하면(오름당 API 2회 왕복) 너무 느리다. 난이도(DB값, API 호출 없음) 기준으로
# 먼저 좁힌 뒤 그 안에서만 실시간 조회 + 랭킹한다.
ENRICH_SAFETY_CAP = 15

DISTANCE_NOTE_STUB = (
    "사용자 위치 좌표가 입력값에 없어 실제 거리 계산 불가 "
    "(현재 tool 입력은 지명 문자열 location만 받음 — 다음 단계에서 "
    "사용자 좌표 파라미터 추가 후 계산 예정)"
)


class AstroLookupError(Exception):
    """KASI 출몰시각 조회 실패."""


class WeatherLookupError(Exception):
    """KMA 단기예보 조회 실패."""


# ───────────────────────── 기존 검증 로직 이식 ─────────────────────────
# test/test_kasi.py, test/Test_combined.py 에서 그대로 가져옴 (재발명 금지).

def hm(s):
    """HHMMSS(앞 공백 가능) → HH:MM. 없으면 '-' (예: 월출 없는 날)."""
    s = (s or "").strip()
    return f"{s[:2]}:{s[2:4]}" if len(s) >= 4 and s.isdigit() else "-"


def deg16(d):
    dirs = ["북", "북북동", "북동", "동북동", "동", "동남동", "남동", "남남동",
            "남", "남남서", "남서", "서남서", "서", "서북서", "북서", "북북서"]
    return dirs[int((float(d) + 11.25) // 22.5) % 16]


def latlon_to_grid(lat, lon):
    """위경도 → 기상청 동네예보 격자번호 (nx, ny). 서울시청(37.5665,126.9780)→(60,127)로 검증됨."""
    RE, GRID = 6371.00877, 5.0
    SLAT1, SLAT2, OLON, OLAT, XO, YO = 30.0, 60.0, 126.0, 38.0, 43, 136
    D = math.pi / 180.0
    re = RE / GRID
    sn = math.log(math.cos(SLAT1 * D) / math.cos(SLAT2 * D)) / math.log(
        math.tan(math.pi * 0.25 + SLAT2 * D * 0.5) / math.tan(math.pi * 0.25 + SLAT1 * D * 0.5))
    sf = math.pow(math.tan(math.pi * 0.25 + SLAT1 * D * 0.5), sn) * math.cos(SLAT1 * D) / sn
    ro = re * sf / math.pow(math.tan(math.pi * 0.25 + OLAT * D * 0.5), sn)
    ra = re * sf / math.pow(math.tan(math.pi * 0.25 + lat * D * 0.5), sn)
    theta = lon * D - OLON * D
    theta = (theta + math.pi) % (2 * math.pi) - math.pi
    theta *= sn
    return int(ra * math.sin(theta) + XO + 0.5), int(ro - ra * math.cos(theta) + YO + 0.5)


def latest_base(now):
    """KMA 단기예보 발표 슬롯(2,5,8,11,14,17,20,23시) 기준 가장 최근 base_date/base_time."""
    t = now - timedelta(minutes=10)
    cand = [s for s in SLOTS if s <= t.hour]
    if cand:
        return t.strftime("%Y%m%d"), f"{max(cand):02d}00"
    return (t - timedelta(days=1)).strftime("%Y%m%d"), "2300"


# ───────────────────────── 신규 헬퍼 ─────────────────────────

def snap_to_hour(dt):
    """목표시각을 최근접 정시로 스냅 (분 30 기준 반올림, 시/일 롤오버 안전 처리)."""
    if dt.minute >= 30:
        dt = dt + timedelta(hours=1)
    return dt.replace(minute=0, second=0, microsecond=0)


def parse_target_datetime(datetime_str, now):
    """None/빈 문자열 → now. ISO 파싱 실패(트레일링 'Z' 등) → 치환 재시도 → 그래도 실패면 now 폴백."""
    if not datetime_str:
        return now, None
    try:
        return datetime.fromisoformat(datetime_str), None
    except ValueError:
        try:
            return datetime.fromisoformat(datetime_str.replace("Z", "+00:00")), None
        except ValueError:
            return now, f"datetime_str '{datetime_str}' 파싱 실패 → 현재 시각 사용"


def hhmm_str_to_datetime(date_str, hhmm):
    """'YYYYMMDD' + 'HH:MM'(또는 '-') → datetime. 값 없으면 None."""
    if not hhmm or hhmm == "-":
        return None
    try:
        h, m = hhmm.split(":")
        return datetime.strptime(date_str, "%Y%m%d").replace(hour=int(h), minute=int(m))
    except (ValueError, IndexError):
        return None


def resolve_max_difficulty(max_difficulty, companion):
    if max_difficulty is not None:  # 0도 유효한 상한이므로 truthiness 체크 금지
        return max_difficulty
    return COMPANION_MAX_DIFFICULTY.get(companion)


def sky_rank(weather_at_target):
    if not weather_at_target:
        return 99
    return SKY_RANK_ORDER.get(weather_at_target.get("sky_code"), 99)


def rank_key(item):
    diff = item["difficulty"] if item["difficulty"] is not None else float("inf")
    return (diff, sky_rank(item["weather_at_target"]))


def make_safety_gate():
    return {"status": "unknown", "reason": "특보 연동 전(스텁)"}
    # TODO(next): 왕복시간 게이트 — (astro.sunset - now) 가용시간 vs round_trip_hint
    # 등급상단값 배수 비교해 pass/warn/block 판정 추가. 이번 단계는 round_trip_hint
    # 텍스트 노출만 하고 실제 게이팅 로직은 미구현.


# ───────────────────────── API 호출 ─────────────────────────

def kasi_times(lat, lon, locdate):
    if not KASI_SERVICE_KEY:
        raise AstroLookupError("환경변수 KASI_SERVICE_KEY 미설정")
    params = {"serviceKey": KASI_SERVICE_KEY, "locdate": locdate,
              "longitude": lon, "latitude": lat, "dnYn": "Y"}
    try:
        r = requests.get(KASI_URL, params=params, timeout=KASI_TIMEOUT)
        r.raise_for_status()
    except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as e:
        raise AstroLookupError(f"KASI 요청 실패: {e}") from e
    try:
        root = ET.fromstring(r.text)
    except ET.ParseError as e:
        raise AstroLookupError(f"KASI XML 파싱 실패: {e}") from e
    code = root.findtext(".//resultCode")
    if code not in ("00", None):
        raise AstroLookupError(f"KASI API 오류 {code}: {root.findtext('.//resultMsg') or r.text[:200]}")
    it = root.find(".//item")
    if it is None:
        raise AstroLookupError("KASI 응답에 item 없음")
    g = lambda t: hm(it.findtext(t, ""))
    return {k: g(k) for k in ("sunrise", "suntransit", "sunset", "moonrise", "moontransit",
                               "moonset", "civilm", "civils")}


def kma_slots(nx, ny, base_date, base_time):
    if not KMA_AUTHKEY:
        raise WeatherLookupError("환경변수 KMA_AUTHKEY 미설정")
    params = dict(pageNo=1, numOfRows=1000, dataType="JSON",
                  base_date=base_date, base_time=base_time, nx=nx, ny=ny, authKey=KMA_AUTHKEY)
    try:
        r = requests.get(KMA_URL, params=params, timeout=KMA_TIMEOUT)
        r.raise_for_status()
    except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as e:
        raise WeatherLookupError(f"KMA 요청 실패: {e}") from e
    try:
        d = r.json()
    except ValueError as e:
        raise WeatherLookupError(f"KMA JSON 파싱 실패: {e}") from e
    try:
        head = d["response"]["header"]
        items = d["response"]["body"]["items"]["item"]
    except (KeyError, TypeError) as e:
        raise WeatherLookupError(f"KMA 응답 형식 이상: {e}") from e
    if head.get("resultCode") != "00":
        raise WeatherLookupError(f"KMA API 오류 {head.get('resultCode')}: {head.get('resultMsg')}")
    slot = {}
    for it in items:
        slot.setdefault((it["fcstDate"], it["fcstTime"]), {})[it["category"]] = it["fcstValue"]
    return slot


# ───────────────────────── 정규화 / verdict ─────────────────────────

def normalize_weather(raw, target_dt):
    if raw is None:
        return None
    vec = raw.get("VEC")
    return {
        "target_time": target_dt.strftime("%Y-%m-%d %H:%M") if target_dt else None,
        "sky_code": raw.get("SKY"), "sky": SKY.get(raw.get("SKY")),
        "pty_code": raw.get("PTY"), "pty": PTY.get(raw.get("PTY")),
        "tmp_c": float(raw["TMP"]) if "TMP" in raw else None,
        "pop_pct": int(float(raw["POP"])) if "POP" in raw else None,
        "wsd_ms": float(raw["WSD"]) if "WSD" in raw else None,
        "vec_deg": float(vec) if vec is not None else None,
        "wind_dir": deg16(vec) if vec is not None else None,
    }


def verdict_sunset_glow(v):
    """test/Test_combined.py verdict() 그대로."""
    label = {"1": "노을·조망 양호", "3": "부분 노을 가능", "4": "노을 기대 낮음"}.get(v.get("SKY", ""), "-")
    out = [label]
    if v.get("POP") and int(float(v["POP"])) >= 40:
        out.append(f"강수확률 {v['POP']}%↑")
    if v.get("WSD"):
        out.append("바람 강함(촬영 주의)" if float(v["WSD"]) >= 9 else "바람 약함")
    return " · ".join(out)


def verdict_view(v):
    """sunset/sunrise가 아닌 목적('조망')용 언어. 구조는 verdict_sunset_glow와 동일."""
    label = {"1": "조망 양호", "3": "부분 시야 확보", "4": "조망 기대 낮음"}.get(v.get("SKY", ""), "-")
    out = [label]
    if v.get("POP") and int(float(v["POP"])) >= 40:
        out.append(f"강수확률 {v['POP']}%↑ (시야 방해 가능)")
    if v.get("WSD"):
        out.append("바람 강함(체감 주의)" if float(v["WSD"]) >= 9 else "바람 약함")
    return " · ".join(out)


def select_verdict_fn(purpose):
    return verdict_sunset_glow if purpose in ("sunset", "sunrise") else verdict_view


# ───────────────────────── DB 접근 ─────────────────────────

def fetch_candidates(location, max_difficulty):
    """호출마다 connect/close (스레드 간 커넥션 공유 회피). 파라미터 바인딩으로 SQL 인젝션 방지."""
    clauses, params = [], []
    if location:
        clauses.append(f'"{COL_ADDR}" LIKE ?')
        params.append(f"%{location}%")
    if max_difficulty is not None:
        # NULL 난이도는 상한이 걸려있을 때는 "상한 이하임을 확인할 수 없음"으로 취급해 제외한다.
        clauses.append("(difficulty IS NOT NULL AND difficulty <= ?)")
        params.append(max_difficulty)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    q = f'SELECT rowid, * FROM "{TABLE}" {where} ORDER BY rowid'
    with contextlib.closing(sqlite3.connect(DB_PATH)) as con:
        con.row_factory = sqlite3.Row
        return con.execute(q, params).fetchall()


# ───────────────────────── 캐싱 ─────────────────────────

def cached_call(cache, key, fn):
    """성공/예외 모두 캐싱: 같은 grid나 좌표를 공유하는 후보가 같은 실패를 반복 재시도하지 않도록."""
    if key in cache:
        return cache[key], True
    try:
        result = fn()
    except (AstroLookupError, WeatherLookupError) as exc:
        cache[key] = exc
        return exc, False
    cache[key] = result
    return result, False


# ───────────────────────── 후보별 빌드 ─────────────────────────

def build_candidate(row, purpose, requested_dt, base_date, base_time,
                     kasi_cache, kma_cache, notes):
    name = row[COL_NAME]
    lat, lon = row[COL_LAT], row[COL_LON]
    db_nx, db_ny = row[COL_NX], row[COL_NY]

    confidence = {"astro": "no_coords", "weather": "no_grid",
                  "grid_source": None, "cache_hit": {"astro": False, "weather": False}}
    astro = None
    weather_norm = None
    verdict_text = None

    try:
        target_date_str = requested_dt.strftime("%Y%m%d")

        # ---- 천문 ----
        if lat is not None and lon is not None:
            astro_result, hit = cached_call(
                kasi_cache, (lat, lon, target_date_str),
                lambda: kasi_times(lat, lon, target_date_str))
            confidence["cache_hit"]["astro"] = hit
            if isinstance(astro_result, Exception):
                confidence["astro"] = "failed"
                notes.append(f"{name}: 천문 조회 실패 - {astro_result}")
            else:
                astro = astro_result
                confidence["astro"] = "ok"

        # ---- 목표시각 결정 ----
        target_moment = None
        if purpose in ("sunset", "sunrise"):
            if astro is not None:
                target_moment = hhmm_str_to_datetime(target_date_str, astro[purpose])
                if target_moment is None:
                    notes.append(f"{name}: {purpose} 시각 정보 없음 → 기상 매칭 생략")
            else:
                notes.append(f"{name}: 천문 데이터 없어 {purpose} 시각 기준 기상 매칭 생략")
        else:
            target_moment = requested_dt

        # ---- 격자 결정 ----
        nx = ny = None
        if db_nx is not None and db_ny is not None:
            nx, ny = db_nx, db_ny
            confidence["grid_source"] = "db"
        elif lat is not None and lon is not None:
            nx, ny = latlon_to_grid(lat, lon)
            confidence["grid_source"] = "computed"

        # ---- 기상 ----
        if target_moment is not None and nx is not None and ny is not None:
            slots_result, hit = cached_call(
                kma_cache, (nx, ny), lambda: kma_slots(nx, ny, base_date, base_time))
            confidence["cache_hit"]["weather"] = hit
            if isinstance(slots_result, Exception):
                confidence["weather"] = "failed"
                notes.append(f"{name}: 기상 조회 실패 - {slots_result}")
            else:
                snapped = snap_to_hour(target_moment)
                raw = slots_result.get((snapped.strftime("%Y%m%d"), snapped.strftime("%H00")))
                if raw is None:
                    confidence["weather"] = "failed"
                    notes.append(
                        f"{name}: 목표 정시({snapped:%Y-%m-%d %H:%M}) 예보 슬롯 없음"
                        f" (지난 시각이거나 단기예보 범위 밖)")
                else:
                    confidence["weather"] = "ok"
                    weather_norm = normalize_weather(raw, snapped)
                    verdict_text = select_verdict_fn(purpose)(raw)
    except Exception as e:  # 안전망: 후보 하나의 예상 못한 오류가 전체 호출을 죽이지 않도록
        notes.append(f"{name}: 처리 중 알 수 없는 오류 - {e}")

    return {
        "name": name, "lat": lat, "lng": lon,
        "kakao_url": row["카카오맵_url"],
        "difficulty": row["difficulty"],
        "difficulty_label": row["difficulty_label"],
        "round_trip_hint": row["round_trip_hint"],
        "distance_note": DISTANCE_NOTE_STUB,
        "astro": astro,
        "weather_at_target": weather_norm,
        "verdict": verdict_text,
        "gates": {"safety": make_safety_gate()},
        "confidence": confidence,
    }


# ───────────────────────── MCP tool ─────────────────────────

mcp = FastMCP("oreum-recommend")


@mcp.tool()
def recommend_oreum(
    location: str | None = None,
    companion: str | None = None,
    purpose: str | None = None,
    datetime_str: str | None = None,
    max_difficulty: int | None = None,
    limit: int = 3,
) -> dict:
    """제주 오름을 조건에 맞춰 필터·랭킹하고 천문/기상 데이터와 함께 추천한다.

    Args:
        location: 지명(예: "성산"). 지번주소 부분매칭. None이면 전체 오름 대상.
        companion: "child"|"elderly"|"solo" 중 하나. 난이도 상한 결정에 쓰임
            (child/elderly→2, solo/None→무제한). max_difficulty가 있으면 그쪽이 우선.
        purpose: "sunset"|"sunrise"|"view" 중 하나. 목표시각과 verdict 언어를 결정.
            sunset/sunrise면 해당 오름의 KASI 일출몰 시각을, view나 None이면
            datetime_str(또는 현재시각)의 시각을 목표시각으로 쓴다.
        datetime_str: ISO 8601 문자열. None이면 서버의 현재 시각을 쓴다.
        max_difficulty: 난이도 상한(1~5). 지정 시 companion 매핑보다 우선한다.
        limit: 반환할 최대 결과 수.

    Returns:
        dict: query_echo(입력/해석값), count(반환된 결과 수), results[](오름별 상세),
        notes[](필터 사유, 부분 실패, 최종 반환 요약 등 사람이 읽을 메모).
    """
    notes: list[str] = []
    now = datetime.now()

    requested_dt, dt_note = parse_target_datetime(datetime_str, now)
    if dt_note:
        notes.append(dt_note)

    if companion and companion not in COMPANION_MAX_DIFFICULTY:
        notes.append(f"알 수 없는 companion 값 '{companion}' → 난이도 제한 없음으로 처리")
    if purpose and purpose not in ("sunset", "sunrise", "view"):
        notes.append(f"알 수 없는 purpose 값 '{purpose}' → 'view'와 동일하게 처리")

    resolved_max_diff = resolve_max_difficulty(max_difficulty, companion)
    effective_limit = max(0, limit)

    query_echo = {
        "location": location, "companion": companion, "purpose": purpose,
        "datetime_str": datetime_str, "resolved_datetime": requested_dt.isoformat(),
        "max_difficulty": max_difficulty, "resolved_max_difficulty": resolved_max_diff,
        "limit": limit,
    }

    rows = fetch_candidates(location, resolved_max_diff)
    if not rows:
        notes.append("조건에 맞는 오름 후보가 없습니다 (location/난이도 필터를 확인하세요).")
        return {"query_echo": query_echo, "count": 0, "results": [], "notes": notes}

    # 실시간 조회(API 호출) 전, DB에 이미 있는 난이도만으로 먼저 좁힌다 (성능 보호).
    enrich_cap = max(effective_limit * 5, ENRICH_SAFETY_CAP)
    pre_sorted_rows = sorted(
        rows, key=lambda r: (r["difficulty"] if r["difficulty"] is not None else float("inf"), r["rowid"]))
    rows_to_enrich = pre_sorted_rows[:enrich_cap]
    if len(rows) > len(rows_to_enrich):
        notes.append(
            f"후보 {len(rows)}개 중 난이도 기준 상위 {len(rows_to_enrich)}개만 실시간(천문/기상) "
            f"조회 후 랭킹함 (성능 보호). 더 좁혀서(location/max_difficulty) 다시 조회하면 정확도가 높아짐.")

    base_date, base_time = latest_base(now)  # 모든 후보가 공유하는 KMA 발표시각(1회만 계산)
    kasi_cache: dict = {}
    kma_cache: dict = {}

    enriched = [
        build_candidate(row, purpose, requested_dt, base_date, base_time,
                         kasi_cache, kma_cache, notes)
        for row in rows_to_enrich
    ]
    # TODO(next): 난이도/SKY 단순 정렬 대신 가중치 랭킹(바람/강수확률/사용자 선호 가중합 등)으로 교체.
    enriched.sort(key=rank_key)
    results = enriched[:effective_limit]

    notes.append(f"총 {len(rows)}개 후보 중 상위 {len(results)}개 반환.")

    return {"query_echo": query_echo, "count": len(results), "results": results, "notes": notes}


if __name__ == "__main__":
    mcp.run(transport="stdio")

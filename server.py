# -*- coding: utf-8 -*-
"""
제주 오름 추천 MCP 서버 — 0단계 골격.

tool 1개(recommend_oreum) 노출: 사용자 조건으로 오름.db를 필터·랭킹하고,
후보별로 KASI(천문)/KMA(단기예보) 값을 붙여 JSON으로 반환한다.

계산(난이도 상한 결정, 목표시각 스냅, 랭킹, 왕복시간 게이트, 특보 안전 게이트)은 전부 이 파일이
결정론적으로 수행하며 LLM에 위임하지 않는다. 왕복시간 게이트는 정밀 예측이 아니라
difficulty(비고 등급)의 상단값(느린 쪽)으로 근사한, 일몰/일출을 못 맞추는 오름을 보수적으로
걸러내기 위한 판정이다(fail 이어도 후보에서 제외하지 않고 경고만 남긴다). 안전 게이트도 마찬가지로
"특보구역 단위" 근사이며(오름 개별 통제 여부 아님) warning 이어도 후보를 제외하지 않는다.
사용자 좌표 기반 실거리 계산, 가중치 랭킹은 다음 단계 TODO로 남겨둔다(코드 내 주석 참고).

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

# ── 왕복시간 게이트 상수 ──
# 이 게이트는 "정밀 왕복 예측"이 아니라 일몰/일출을 못 맞추는 오름을 보수적으로
# 걸러내기 위한 근사치다. 웹 실측 왕복시간은 오름당 편차 ±2배라 DB의 difficulty
# (비고 등급)만으로 근사하며, 등급 상단값(느린 쪽)을 쓴다. 절대분을 정밀값처럼
# UI에 내세우지 말 것 — 항상 note에 근사임을 남긴다.
DIFFICULTY_ROUND_TRIP_MAX_MIN = {1: 30, 2: 45, 3: 70, 4: 100, 5: 150}
PACE_MULTIPLIER = {"child": 1.8, "elderly": 1.8, "solo": 0.8}
DEFAULT_PACE_MULTIPLIER = 1.0
ROUND_TRIP_PASS_MARGIN_MIN = 30  # margin >= 이 값 -> pass, 0~미만 -> tight, 미만 -> fail
SAFE_NIGHT_MAX_DIFFICULTY = 2  # 야간 특례에서 "안전한 오름"으로 안내하는 난이도 상한(note용, 필터 아님)

ROUND_TRIP_STATUS_PRIORITY = {"pass": 0, "n/a": 0, "tight": 1, "night": 2, "fail": 3, "unknown": 4}

# ── 특보(안전) 게이트 상수 ──
# 특보구역은 산지/중산간처럼 여러 오름을 묶는 넓은 단위라, 이 게이트는 "오름 개별 안전 확인"이
# 아니라 "그 특보구역에 입산/탐방 위험 특보가 발효 중인지"를 보수적으로 알려주는 참고 정보다.
# 특보 ≠ 물리적 폐쇄이므로 status 는 "clear"/"warning"/"unknown"만 쓴다("blocked" 금지).
# API 호출/키 실패는 위험이 아니라 "정보 없음"으로 다뤄 status="unknown"으로 게이트를 통과시킨다.
KMA_SAFETY_URL = "https://apihub.kma.go.kr/api/typ01/url/wrn_now_data_new.php"
KMA_SAFETY_TIMEOUT = 15

# 입산/탐방 위험과 직결되는 특보만 남긴다. 폭염/열대야/황사/건조/안개는 입산 통제와 무관한
# 노이즈라 제외한다. 응답의 WRN이 한글/코드 어느 쪽으로 오는지 확실치 않아 둘 다 넣어둔다.
SAFETY_RELEVANT_WRN = {
    "강풍", "풍랑", "호우", "대설", "태풍", "폭풍해일", "한파",
    "W", "V", "R", "S", "T", "O", "C",
}
CONFIDENCE_SAFETY_NOTE = "특보구역 단위(오름 개별 아님), 정상 표고 기준 매핑"
# clear/unknown 은 "위험 정보 없음"으로 동급 취급, warning만 뒤로 민다.
SAFETY_STATUS_PRIORITY = {"clear": 0, "unknown": 0, "warning": 1}

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


class SafetyLookupError(Exception):
    """KMA 특보현황 조회 실패."""


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
    """정렬: 특보 안전 게이트(clear/unknown < warning) 최우선, 그다음 왕복 게이트 상태
    (pass≈n/a < tight < night < fail < unknown), 그다음 기존 기준.

    n/a(마감 시각 없음 — 게이트 미적용)는 감점 사유가 아니므로 pass와 동일 우선순위를 쓴다.
    night 상태는 게이트가 무의미하므로 그룹 내에서는 기존 2번째 키(difficulty 오름차순)가
    그대로 "안전한(쉬운) 오름 우선" 가점 역할을 한다 — 별도 키 불필요.
    """
    diff = item["difficulty"] if item["difficulty"] is not None else float("inf")
    safety_status = (item.get("gates") or {}).get("safety") or {}
    safety_priority = SAFETY_STATUS_PRIORITY.get(safety_status.get("status"), 0)
    rt_status = (item.get("gates") or {}).get("round_trip") or {}
    rt_priority = ROUND_TRIP_STATUS_PRIORITY.get(rt_status.get("status"), 5)
    return (safety_priority, rt_priority, diff, sky_rank(item["weather_at_target"]))


def parse_safety_text(text):
    """wrn_now_data_new 원문(CSV형 텍스트) → [{reg_id, reg_up, reg_ko, wrn, lvl}, ...].

    '#'로 시작하는 줄(주석/헤더)은 전부 스킵. 데이터 줄은 ','로 split 후 각 필드 .strip()
    (값에 앞뒤 공백/전각공백이 흔하다). 필드 9개 미만이거나 REG_ID/WRN이 빈 줄은 스킵.
    컬럼 순서(고정): [0]REG_UP [1]REG_UP_KO [2]REG_ID [3]REG_KO [4]TM_FC [5]TM_EF [6]WRN [7]LVL [8]CMD.
    파싱 결과 0건이어도 예외 아님 — "특보 없음"으로 정상 처리(호출부 책임).
    """
    warnings = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        fields = [f.strip() for f in line.split(",")]
        if len(fields) < 9:
            continue
        reg_up, _reg_up_ko, reg_id, reg_ko, _tm_fc, _tm_ef, wrn, lvl, _cmd = fields[:9]
        if not reg_id or not wrn:
            continue
        warnings.append({"reg_id": reg_id, "reg_up": reg_up, "reg_ko": reg_ko, "wrn": wrn, "lvl": lvl})
    return warnings


def filter_safety_relevant(warnings):
    """입산/탐방 위험과 무관한 특보(폭염/열대야/황사/건조/안개 등)를 제외한다."""
    return [w for w in warnings if w["wrn"] in SAFETY_RELEVANT_WRN]


def fetch_active_safety_warnings():
    """특보현황 1회 호출 + 파싱 + 입산관련 필터. 실패 시 SafetyLookupError를 그대로 전파한다
    (호출부에서 status=unknown 으로 처리 — API 실패를 위험으로 취급하지 않는다).
    반환값은 (발효중인 입산관련 특보 목록, HTTP status code) — status는 notes 로깅용."""
    text, status_code = kma_safety_now()
    return filter_safety_relevant(parse_safety_text(text)), status_code


def _safety_row_matches(w, warn_reg_id, warn_reg_up):
    """말단코드(warn_reg_id) 우선 대조, 상위코드(warn_reg_up)로도 대조한다 — 특보가 상위
    구역 단위로만 발효되는 경우가 있기 때문."""
    if warn_reg_id and w["reg_id"] == warn_reg_id:
        return True
    if warn_reg_up and (w["reg_id"] == warn_reg_up or w["reg_up"] == warn_reg_up):
        return True
    return False


def build_safety_detail(matched):
    """중복 제거한 '{구역명} {특보종류}{등급}' 목록을 이어붙여 사람이 읽을 문구로."""
    parts = []
    for w in matched:
        part = f"{w['reg_ko']} {w['wrn']}{w['lvl']}"
        if part not in parts:
            parts.append(part)
    return ", ".join(parts) + " 발효"


def match_safety_gate(warn_reg_id, warn_reg_up, active_warnings):
    """오름의 특보구역코드를 발효 중인 입산관련 특보 목록과 대조해 gates.safety를 만든다.

    active_warnings가 None이면(특보현황 API 호출 자체 실패) "위험 없음"이 아니라
    "정보 없음(unknown)"으로 게이트를 통과시킨다.
    """
    if active_warnings is None:
        return {"status": "unknown", "detail": "특보 조회 실패", "warnings": []}
    matched = [w for w in active_warnings if _safety_row_matches(w, warn_reg_id, warn_reg_up)]
    if not matched:
        return {"status": "clear", "detail": "발효 중인 입산 관련 특보 없음", "warnings": []}
    return {
        "status": "warning",
        "detail": build_safety_detail(matched),
        "warnings": [{"type": w["wrn"], "level": w["lvl"], "reg": w["reg_ko"]} for w in matched],
    }


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


def kma_safety_now():
    """특보현황(wrn_now_data_new) 원문 조회. 응답은 JSON이 아니라 CSV형 '텍스트'다.

    apihub typ01(php) 엔드포인트는 User-Agent 없는 요청을 403으로 거부하는 것으로 보여
    브라우저 UA를 붙여 호출한다. 그래도 403이면 authKey를 params dict가 아니라 URL
    쿼리스트링에 직접 붙여 재시도한다(요청 라이브러리의 이중 URL-encoding 회피).
    반환값은 (원문 텍스트, 최종 status code) — 호출부가 성공 시에도 status를 notes에
    남겨 디버깅할 수 있게 한다.
    """
    if not KMA_AUTHKEY:
        raise SafetyLookupError("환경변수 KMA_AUTHKEY 미설정")
    headers = {"User-Agent": "Mozilla/5.0"}
    params = {"fe": "f", "tm": "", "disp": "0", "help": "1", "authKey": KMA_AUTHKEY}
    try:
        r = requests.get(KMA_SAFETY_URL, params=params, headers=headers, timeout=KMA_SAFETY_TIMEOUT)
        if r.status_code == 403:
            url = f"{KMA_SAFETY_URL}?fe=f&tm=&disp=0&help=1&authKey={KMA_AUTHKEY}"
            r = requests.get(url, headers=headers, timeout=KMA_SAFETY_TIMEOUT)
        r.raise_for_status()
    except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as e:
        status = getattr(getattr(e, "response", None), "status_code", "?")
        raise SafetyLookupError(f"특보현황 요청 실패(status={status}): {e}") from e
    return r.text, r.status_code


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


# ───────────────────────── 왕복시간 게이트 ─────────────────────────

def companion_pace_label(companion):
    return {"child": "아이 동반", "elderly": "어르신 동반", "solo": "단독"}.get(companion, "일반")


def estimate_round_trip_min(difficulty, companion):
    """difficulty(1~5) 등급 상단값 × 동반자 페이스 배수 → 왕복 예상분(보수적 근사)."""
    if difficulty is None:
        return None
    cap_min = DIFFICULTY_ROUND_TRIP_MAX_MIN.get(difficulty)
    if cap_min is None:
        return None
    pace = PACE_MULTIPLIER.get(companion, DEFAULT_PACE_MULTIPLIER)
    return round(cap_min * pace)


def round_trip_status(margin_min):
    if margin_min >= ROUND_TRIP_PASS_MARGIN_MIN:
        return "pass"
    if margin_min >= 0:
        return "tight"
    return "fail"


def resolve_target_and_gate(name, purpose, requested_dt, astro_today, target_date_str,
                             lat, lon, difficulty, companion, kasi_cache, notes,
                             explicit_dt=True, kasi_fetch=kasi_times):
    """목표시각(target_moment)과 왕복 게이트(gates.round_trip)를 함께 정한다.

    반환된 target_moment 은 기상(KMA) 슬롯 매칭에도 그대로 재사용해, 게이트가 본 시각과
    verdict/weather_at_target 이 본 시각이 어긋나지 않게 한다.

    목표시각(deadline)은 다음 중 하나일 때만 성립한다:
      - purpose in ("sunset", "sunrise") → 해당 일몰/일출 시각.
      - purpose in ("view", None) 이면서 explicit_dt=True(호출자가 datetime_str 을
        실제로 줬음, now() 폴백이 아님) → requested_dt 그 자체.
    그 외(= purpose 없고 datetime_str 도 없어 그냥 지금 감)는 deadline 이 없으므로
    왕복 게이트를 적용하지 않는다(status="n/a"). explicit_dt 는 서버가 now()로 채운 것과
    사용자가 실제로 명시한 시각을 구분하기 위한 플래그다 — now()를 deadline으로 오인하지
    않기 위해 반드시 필요하다.

    purpose 별 목표시각 정책:
      - sunset/sunrise: 오늘 KASI 값이 기본. requested_dt(=현재시각 취급)가 이미 그 시각을
        지났으면 내일 날짜로 KASI 를 재조회해 내일 값을 목표로 삼는다(오늘 값 재사용 금지).
      - view/None + explicit_dt: requested_dt 자체가 목표시각. 다만 그 시각이 오늘
        일몰~일출 사이(야간)이면 시간 게이트가 무의미하므로 status="night" 로 대체한다
        (안전한 오름 우선 안내는 note로만).
    """
    pace_label = companion_pace_label(companion)
    base_note = f"{pace_label} 페이스 기준, 비고 등급 근사"
    est_min = estimate_round_trip_min(difficulty, companion)

    target_moment = None
    target_basis = None
    night = False

    if purpose in ("sunset", "sunrise"):
        if astro_today is None:
            notes.append(f"{name}: 천문 데이터 없어 {purpose} 목표시각 산정 불가 → 왕복 게이트 미산정")
        else:
            t_today = hhmm_str_to_datetime(target_date_str, astro_today[purpose])
            if t_today is None:
                notes.append(f"{name}: {purpose} 시각 정보 없음 → 왕복 게이트 미산정")
            elif requested_dt > t_today:
                purpose_kr = "일몰" if purpose == "sunset" else "일출"
                tomorrow_date_str = (requested_dt + timedelta(days=1)).strftime("%Y%m%d")
                astro_tomorrow, hit = cached_call(
                    kasi_cache, (lat, lon, tomorrow_date_str),
                    lambda: kasi_fetch(lat, lon, tomorrow_date_str))
                if isinstance(astro_tomorrow, Exception):
                    notes.append(f"{name}: 내일 천문 재조회 실패 - {astro_tomorrow} → 왕복 게이트 미산정")
                else:
                    t_tomorrow = hhmm_str_to_datetime(tomorrow_date_str, astro_tomorrow[purpose])
                    if t_tomorrow is None:
                        notes.append(f"{name}: 내일 {purpose_kr} 시각 정보 없음 → 왕복 게이트 미산정")
                    else:
                        target_moment = t_tomorrow
                        target_basis = f"{purpose}_tomorrow"
                        base_note += f" · 오늘 {purpose_kr} 지나 내일 기준"
            else:
                target_moment = t_today
                target_basis = f"{purpose}_today"
    elif not explicit_dt:
        # 마감(deadline) 자체가 없는 경우 — 왕복 게이트를 적용하지 않는다. requested_dt는
        # 기상(KMA) 슬롯 매칭용으로만 재사용하고, target_time 은 게이트 응답에서 null로 낸다.
        gate = {
            "status": "n/a",
            "est_min": est_min,
            "remaining_min": None,
            "margin_min": None,
            "target_time": None,
            "target_basis": "no_deadline",
            "note": "마감 시각 없음 — 왕복 게이트 미적용(참고 소요시간만)",
        }
        return requested_dt, gate
    else:
        target_moment = requested_dt
        target_basis = "user_time"
        if astro_today is None:
            notes.append(f"{name}: 천문 데이터 없어 야간 여부 판정 불가")
        else:
            sunset_today = hhmm_str_to_datetime(target_date_str, astro_today["sunset"])
            sunrise_today = hhmm_str_to_datetime(target_date_str, astro_today["sunrise"])
            if sunset_today and sunrise_today and (target_moment > sunset_today or target_moment < sunrise_today):
                night = True
                target_basis = "night"
                base_note += f" · 야간 시간대 — 안전한 오름 우선(비고 등급 {SAFE_NIGHT_MAX_DIFFICULTY} 이하 권장)"

    if target_moment is None:
        gate = {
            "status": "unknown", "est_min": est_min, "remaining_min": None, "margin_min": None,
            "target_time": None, "target_basis": None, "note": base_note + " · 목표시각 산정 불가",
        }
        return None, gate

    remaining_min = round((target_moment - requested_dt).total_seconds() / 60)

    if night:
        status = "night"
        margin_min = None if est_min is None else remaining_min - est_min
    elif est_min is None:
        status = "unknown"
        margin_min = None
        base_note += " · 난이도 정보 없어 소요시간 추정 불가"
    else:
        margin_min = remaining_min - est_min
        status = round_trip_status(margin_min)

    gate = {
        "status": status,
        "est_min": est_min,
        "remaining_min": remaining_min,
        "margin_min": margin_min,
        "target_time": target_moment.strftime("%Y-%m-%dT%H:%M"),
        "target_basis": target_basis,
        "note": base_note,
    }
    return target_moment, gate


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

def build_candidate(row, purpose, requested_dt, base_date, base_time, companion,
                     kasi_cache, kma_cache, notes, active_warnings, explicit_dt):
    name = row[COL_NAME]
    lat, lon = row[COL_LAT], row[COL_LON]
    db_nx, db_ny = row[COL_NX], row[COL_NY]
    difficulty = row["difficulty"]

    safety_gate = match_safety_gate(row["warn_reg_id"], row["warn_reg_up"], active_warnings)

    confidence = {"astro": "no_coords", "weather": "no_grid", "safety": CONFIDENCE_SAFETY_NOTE,
                  "grid_source": None, "cache_hit": {"astro": False, "weather": False}}
    astro = None
    weather_norm = None
    verdict_text = None
    round_trip_gate = None

    try:
        target_date_str = requested_dt.strftime("%Y%m%d")

        # ---- 천문(오늘) ----
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

        # ---- 목표시각 결정 + 왕복 게이트 ----
        # (내일로 넘어갈 수 있어 여기서 기상 슬롯 매칭에 쓸 target_moment 도 함께 정한다)
        target_moment, round_trip_gate = resolve_target_and_gate(
            name, purpose, requested_dt, astro, target_date_str,
            lat, lon, difficulty, companion, kasi_cache, notes, explicit_dt=explicit_dt)

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

    if round_trip_gate is None:
        round_trip_gate = {
            "status": "unknown", "est_min": estimate_round_trip_min(difficulty, companion),
            "remaining_min": None, "margin_min": None, "target_time": None,
            "target_basis": None, "note": "처리 중 오류로 왕복 게이트 미산정",
        }

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
        "gates": {"safety": safety_gate, "round_trip": round_trip_gate},
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
        results[].gates.round_trip 은 difficulty 등급 근사 기반 왕복시간 게이트
        (status: pass/tight/fail/night/n/a/unknown)다 — 정밀 예측이 아니라 목표시각(일몰/일출/
        지정시각)을 못 맞출 후보를 보수적으로 걸러내기 위한 값이며, fail 이어도 후보는
        제외하지 않고 목록에 남는다(정렬만 뒤로 밀림). purpose도 없고 datetime_str도 없어
        마감 시각 자체가 없는 경우(그냥 지금 감)는 게이트를 적용하지 않고 status="n/a"로
        표시한다(감점 없음, pass와 동급).
    """
    notes: list[str] = []
    now = datetime.now()

    requested_dt, dt_note = parse_target_datetime(datetime_str, now)
    if dt_note:
        notes.append(dt_note)
    # datetime_str 이 실제로 주어졌고(now() 폴백이 아니고) 파싱도 성공했을 때만 "명시적
    # 목표시각"으로 취급한다 — 서버가 now()로 채운 값을 목표시각(deadline)으로 오인하지 않기 위함.
    explicit_dt = bool(datetime_str) and dt_note is None

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

    # 특보현황은 후보마다 부르지 않고 요청당 1회만 호출해 전체 발효목록을 얻은 뒤,
    # 각 오름은 그 목록과 메모리에서만 대조한다.
    try:
        active_warnings, safety_status_code = fetch_active_safety_warnings()
        notes.append(f"특보현황 조회 성공 (status={safety_status_code}, 입산관련 {len(active_warnings)}건)")
    except SafetyLookupError as e:
        active_warnings = None
        notes.append(f"특보현황 조회 실패 - {e} → 안전 게이트는 전체 unknown 처리(위험으로 취급하지 않음)")

    enriched = [
        build_candidate(row, purpose, requested_dt, base_date, base_time, companion,
                         kasi_cache, kma_cache, notes, active_warnings, explicit_dt)
        for row in rows_to_enrich
    ]
    # TODO(next): 난이도/SKY 단순 정렬 대신 가중치 랭킹(바람/강수확률/사용자 선호 가중합 등)으로 교체.
    enriched.sort(key=rank_key)
    results = enriched[:effective_limit]

    notes.append(f"총 {len(rows)}개 후보 중 상위 {len(results)}개 반환.")

    return {"query_echo": query_echo, "count": len(results), "results": results, "notes": notes}


if __name__ == "__main__":
    mcp.run(transport="stdio")

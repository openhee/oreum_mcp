# -*- coding: utf-8 -*-
"""
오름.db(SQLite) 기반 — 랜덤 오름 1개의 저녁 6시(18:30 근방) 단기예보 조회.

CSV 대신 DB 사용. SQLite는 내부 UTF-8 고정이라 인코딩 꼬임 없음.
실행 전: AUTHKEY 채우기 / 아래 '컬럼 매핑'을 실제 스키마에 맞추기.
실행:  pip install requests  →  python3 test_forecast_db.py
"""
import sqlite3, math, os, random, sys, io
from datetime import datetime, timedelta
import requests

# 윈도우 콘솔 한글 깨짐 방지 (출력 인코딩만 UTF-8로)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

AUTHKEY = "_I4hZYaDRQaOIWWGg2UGjQ"

BASE = os.path.dirname(os.path.abspath(__file__))
DB   = os.path.join(BASE, "오름.db")
URL  = "https://apihub.kma.go.kr/api/typ02/openApi/VilageFcstInfoService_2.0/getVilageFcst"

# ── 컬럼 매핑 (실제 스키마에 맞게 수정) ─────────────────
TABLE   = "오름"        # 테이블명
COL_NAME = "오름명"
COL_LAT  = "위도"
COL_LON  = "경도"
COL_NX   = "grid_nx"     # 없으면 None 으로 두면 위경도로 즉석 변환
COL_NY   = "grid_ny"
# ────────────────────────────────────────────────

SLOTS = [2, 5, 8, 11, 14, 17, 20, 23]
SKY = {"1": "맑음", "3": "구름많음", "4": "흐림"}
PTY = {"0": "없음", "1": "비", "2": "비/눈", "3": "눈", "4": "소나기"}

def latlon_to_grid(lat, lon):
    RE, GRID = 6371.00877, 5.0
    SLAT1, SLAT2, OLON, OLAT, XO, YO = 30.0, 60.0, 126.0, 38.0, 43, 136
    D = math.pi / 180.0
    re = RE / GRID
    sn = math.log(math.cos(SLAT1*D)/math.cos(SLAT2*D)) / math.log(
        math.tan(math.pi*0.25+SLAT2*D*0.5)/math.tan(math.pi*0.25+SLAT1*D*0.5))
    sf = math.pow(math.tan(math.pi*0.25+SLAT1*D*0.5), sn)*math.cos(SLAT1*D)/sn
    ro = re*sf/math.pow(math.tan(math.pi*0.25+OLAT*D*0.5), sn)
    ra = re*sf/math.pow(math.tan(math.pi*0.25+lat*D*0.5), sn)
    theta = lon*D - OLON*D
    theta = (theta + math.pi) % (2*math.pi) - math.pi
    theta *= sn
    return int(ra*math.sin(theta)+XO+0.5), int(ro-ra*math.cos(theta)+YO+0.5)

def deg16(d):
    dirs = ["북","북북동","북동","동북동","동","동남동","남동","남남동",
            "남","남남서","남서","서남서","서","서북서","북서","북북서"]
    return dirs[int((float(d)+11.25)//22.5) % 16]

def latest_base(now):
    t = now - timedelta(minutes=10)
    cand = [s for s in SLOTS if s <= t.hour]
    if cand:
        return t.strftime("%Y%m%d"), f"{max(cand):02d}00"
    return (t - timedelta(days=1)).strftime("%Y%m%d"), "2300"

def pick_oreum():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    cols = f'"{COL_NAME}","{COL_LAT}","{COL_LON}"'
    if COL_NX and COL_NY:
        cols += f',"{COL_NX}","{COL_NY}"'
    q = (f'SELECT {cols} FROM "{TABLE}" '
         f'WHERE "{COL_LAT}" IS NOT NULL AND "{COL_LAT}" != "" '
         f'AND "{COL_LON}" IS NOT NULL AND "{COL_LON}" != ""')
    rows = con.execute(q).fetchall()
    con.close()
    if not rows:
        sys.exit("좌표 있는 오름이 없음.")
    r = random.choice(rows)
    lat, lon = float(r[COL_LAT]), float(r[COL_LON])
    if COL_NX and COL_NY and r[COL_NX] not in (None, "",):
        nx, ny = int(r[COL_NX]), int(r[COL_NY])
    else:
        nx, ny = latlon_to_grid(lat, lon)
    return r[COL_NAME], lat, lon, nx, ny

def main():
    if AUTHKEY.startswith("여기에"):
        sys.exit("먼저 AUTHKEY 를 채워라.")

    name, lat, lon, nx, ny = pick_oreum()
    base_date, base_time = latest_base(datetime.now())
    print(f"[오름] {name}  ({lat}, {lon}) → 격자 nx={nx}, ny={ny}")
    print(f"[발표] {base_date} {base_time}\n")

    params = dict(pageNo=1, numOfRows=1000, dataType="JSON",
                  base_date=base_date, base_time=base_time, nx=nx, ny=ny,
                  authKey=AUTHKEY)
    res = requests.get(URL, params=params, timeout=15)
    res.raise_for_status()
    data = res.json()

    head = data["response"]["header"]
    if head["resultCode"] != "00":
        sys.exit(f"API 오류: {head['resultCode']} {head['resultMsg']} "
                 f"(키 미인증/미활용신청 가능성)")

    items = data["response"]["body"]["items"]["item"]
    slot = {}
    for it in items:
        slot.setdefault((it["fcstDate"], it["fcstTime"]), {})[it["category"]] = it["fcstValue"]

    days = sorted({d for (d, t) in slot if t in ("1800", "1900")})
    if not days:
        sys.exit("응답에 18~19시 슬롯 없음 (latest_base minutes=45 로 시도).")
    day = days[0]

    print(f"[예보] {day} — 18:30 전후 (일몰 무렵)")
    for t in ("1800", "1900"):
        v = slot.get((day, t))
        if not v:
            continue
        line = f"  {t[:2]}:{t[2:]}  하늘 {SKY.get(v.get('SKY',''),'-')}"
        if "TMP" in v: line += f" | 기온 {v['TMP']}℃"
        if "POP" in v: line += f" | 강수확률 {v['POP']}%"
        if "PTY" in v: line += f" | 강수 {PTY.get(v['PTY'],'-')}"
        if "WSD" in v:
            line += f" | 바람 {v['WSD']}m/s"
            if "VEC" in v: line += f" {deg16(v['VEC'])}풍"
        if "REH" in v: line += f" | 습도 {v['REH']}%"
        print(line)

if __name__ == "__main__":
    main()
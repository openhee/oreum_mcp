# -*- coding: utf-8 -*-
"""
오름.db 에 기상특보구역 컬럼을 매핑·적재 (D축 안전 게이트용).

추가 컬럼:
  warn_reg_id    TEXT  특보구역 말단코드 (예 L1091330)  ← 런타임 REG_ID 매칭용
  warn_reg_name  TEXT  특보구역명 (예 제주시동부)
  warn_reg_up    TEXT  상위 특보구역코드 (예 L1091300)  ← REG_UP 보조 매칭용

매핑 규칙 (기상청 제주 특보구역 정의):
  1) 정상 표고 ≥ 600         → 제주도산지 (L1090500)
  2) 200 ≤ 표고 < 600        → 제주시중산간(L1091340) / 서귀포시중산간(L1091440)
  3) 표고 < 200 또는 결측     → 읍면동으로 저지대 6개 구역
검증: 실제 특보응답 REG_ID가 이 말단코드로 옴을 확인함(2026-07-27 폭염 특보).

주의(confidence):
  - 표고는 '정상' 기준이라 산자락 저지대 오름이 중산간/산지로 과분류될 수 있음.
    안전 방향(더 위험하게)이라 게이트엔 무해. UI엔 "정상 표고 기준" 명시 권장.

실행: python3 fill_warn_region_db.py   (재실행 안전)
"""
import sqlite3, os, sys, io, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

BASE = os.path.dirname(os.path.abspath(__file__))
DB   = os.path.join(BASE, "오름.db")
TABLE, COL_NAME, COL_ADDR, COL_ELEV = "오름", "오름명", "지번주소", "표고"

# 구역명 → (말단코드, 상위코드)
WARN = {
    "제주도산지":   ("L1090500", "L1090000"),
    "제주시서부":   ("L1091310", "L1091300"),
    "제주시북부":   ("L1091320", "L1091300"),
    "제주시동부":   ("L1091330", "L1091300"),
    "제주시중산간": ("L1091340", "L1091300"),
    "서귀포시서부": ("L1091410", "L1091400"),
    "서귀포시남부": ("L1091420", "L1091400"),
    "서귀포시동부": ("L1091430", "L1091400"),
    "서귀포시중산간":("L1091440", "L1091400"),
}
# 저지대(<200m) 읍면동 → 구역 (동지역 등 미포함분은 기본값으로 흡수)
JEJU_LOW = {"애월읍": "제주시북부", "조천읍": "제주시북부",
            "구좌읍": "제주시동부", "우도면": "제주시동부",
            "한림읍": "제주시서부", "한경면": "제주시서부"}
SEOG_LOW = {"안덕면": "서귀포시남부", "남원읍": "서귀포시남부",
            "성산읍": "서귀포시동부", "표선면": "서귀포시동부",
            "대정읍": "서귀포시서부"}
DEFAULT = {"제주시": "제주시북부", "서귀포시": "서귀포시남부"}  # 동지역 등

def admin_si(addr):
    if "제주시" in (addr or ""):   return "제주시"
    if "서귀포시" in (addr or ""): return "서귀포시"
    return ""

def eupmyeondong(addr):
    m = re.findall(r"([가-힣]+(?:읍|면|동))", addr or "")
    return m[0] if m else ""

def classify(addr, elev):
    si = admin_si(addr)
    e  = eupmyeondong(addr)
    try:
        h = float(str(elev).strip())
    except (TypeError, ValueError):
        h = None
    # 1) 산지
    if h is not None and h >= 600:
        return "제주도산지"
    # 2) 중산간
    if h is not None and 200 <= h < 600:
        return "제주시중산간" if si == "제주시" else "서귀포시중산간"
    # 3) 저지대(<200) 또는 표고결측 → 읍면동
    if si == "제주시":
        return JEJU_LOW.get(e, DEFAULT["제주시"])
    if si == "서귀포시":
        return SEOG_LOW.get(e, DEFAULT["서귀포시"])
    return None  # 시 판별 불가(비정상 주소)

def ensure_column(con, col):
    cols = [r[1] for r in con.execute(f'PRAGMA table_info("{TABLE}")')]
    if col not in cols:
        con.execute(f'ALTER TABLE "{TABLE}" ADD COLUMN "{col}" TEXT')
        print(f"  + 컬럼 추가: {col}")
    else:
        print(f"  = 컬럼 존재: {col} (갱신)")

def main():
    if not os.path.exists(DB):
        sys.exit(f"DB 없음: {DB}")
    con = sqlite3.connect(DB)

    print("[컬럼 준비]")
    for c in ("warn_reg_id", "warn_reg_name", "warn_reg_up"):
        ensure_column(con, c)

    rows = con.execute(
        f'SELECT rowid, "{COL_NAME}", "{COL_ADDR}", "{COL_ELEV}" FROM "{TABLE}"').fetchall()
    from collections import Counter
    dist = Counter()
    done = skipped = 0
    for rowid, name, addr, elev in rows:
        zone = classify(addr, elev)
        if zone is None:
            con.execute(
                f'UPDATE "{TABLE}" SET warn_reg_id=NULL, warn_reg_name=NULL, warn_reg_up=NULL '
                f'WHERE rowid=?', (rowid,))
            skipped += 1
            print(f"  ! 시 판별 불가 → NULL: {name} ({addr})")
            continue
        code, up = WARN[zone]
        con.execute(
            f'UPDATE "{TABLE}" SET warn_reg_id=?, warn_reg_name=?, warn_reg_up=? WHERE rowid=?',
            (code, zone, up, rowid))
        dist[zone] += 1
        done += 1
    con.commit()

    print(f"\n[적재] {done}건 / 스킵 {skipped}건")
    for zone, (code, up) in WARN.items():
        print(f"  {zone:12s}{code}  {dist[zone]}개")

    # 검증 샘플: 구역별 1건씩
    print("\n[검증 샘플]")
    for zone in WARN:
        r = con.execute(
            f'SELECT "{COL_NAME}","{COL_ELEV}",warn_reg_id,warn_reg_name,warn_reg_up '
            f'FROM "{TABLE}" WHERE warn_reg_name=? LIMIT 1', (zone,)).fetchone()
        if r:
            print("  ", r)

    con.close()
    print(f"\n완료 → {DB}")
    print("※ 런타임: wrn_now_data_new 응답의 REG_ID를 warn_reg_id와,"
          " REG_ID/REG_UP를 warn_reg_up과도 대조(둘 중 하나 매칭이면 특보).")

if __name__ == "__main__":
    main()
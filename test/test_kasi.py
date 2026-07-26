# -*- coding: utf-8 -*-
"""
KASI(한국천문연구원) 출몰시각 API 단독 테스트.
- 오름.db에서 랜덤 오름 1개 → getLCRiseSetInfo 로 오늘 일출/일몰/월출/월몰/박명 조회.
- 목적: serviceKey 검증 + XML 파싱 확인. (기상 통합은 다음 단계)

실행 전: SERVICE_KEY 에 data.go.kr '일반 인증키(Decoding)' 붙여넣기.
실행:  pip install requests  →  python3 test_kasi.py
"""
import sqlite3, os, sys, io, random
import xml.etree.ElementTree as ET
from datetime import datetime
import requests

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")  # 윈도우 콘솔 한글

# data.go.kr '일반 인증키(Decoding)' — Encoding 키 아님! (+, = 그대로인 것)
SERVICE_KEY = "36d19111797705138bf25160862bd78ffe330ee41182657b3fcdafab028a4a8c"

BASE = os.path.dirname(os.path.abspath(__file__))
DB   = os.path.join(BASE, "오름.db")
URL  = "http://apis.data.go.kr/B090041/openapi/service/RiseSetInfoService/getLCRiseSetInfo"

TABLE, COL_NAME, COL_LAT, COL_LON = "오름", "오름명", "위도", "경도"

def hm(s):
    """HHMMSS(앞 공백 가능) → HH:MM. 없으면 '-' (예: 월출 없는 날)."""
    s = (s or "").strip()
    return f"{s[:2]}:{s[2:4]}" if len(s) >= 4 and s.isdigit() else "-"

def pick_oreum():
    con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
    q = (f'SELECT "{COL_NAME}","{COL_LAT}","{COL_LON}" FROM "{TABLE}" '
         f'WHERE "{COL_LAT}" IS NOT NULL AND "{COL_LAT}" != "" '
         f'AND "{COL_LON}" IS NOT NULL AND "{COL_LON}" != ""')
    rows = con.execute(q).fetchall(); con.close()
    if not rows:
        sys.exit("좌표 있는 오름이 없음.")
    r = random.choice(rows)
    return r[COL_NAME], float(r[COL_LAT]), float(r[COL_LON])

def main():
    if SERVICE_KEY.startswith("여기에"):
        sys.exit("먼저 SERVICE_KEY(Decoding) 를 채워라.")

    name, lat, lon = pick_oreum()
    locdate = datetime.now().strftime("%Y%m%d")
    print(f"[오름] {name}  (위도 {lat}, 경도 {lon})")
    print(f"[날짜] {locdate}\n")

    params = {
        "serviceKey": SERVICE_KEY,   # Decoding 키 → requests가 1회만 인코딩
        "locdate":   locdate,
        "longitude": lon,            # dnYn=Y 라 소수 그대로
        "latitude":  lat,
        "dnYn":      "Y",
    }
    res = requests.get(URL, params=params, timeout=15)
    res.raise_for_status()

    try:
        root = ET.fromstring(res.text)
    except ET.ParseError:
        sys.exit(f"XML 파싱 실패. 응답 앞부분:\n{res.text[:400]}")

    code = root.findtext(".//resultCode") or root.findtext(".//returnReasonCode")
    if code not in ("00", None):
        msg = root.findtext(".//resultMsg") or root.findtext(".//returnAuthMsg") or res.text[:300]
        sys.exit(f"API 오류: {code} {msg}\n(키 미인증/이중인코딩/활용신청 확인)")

    it = root.find(".//item")
    if it is None:
        sys.exit(f"item 없음. 응답:\n{res.text[:400]}")
    g = lambda t: it.findtext(t, "")

    print(f"[천문] 일출 {hm(g('sunrise'))} · 일남중 {hm(g('suntransit'))} · 일몰 {hm(g('sunset'))}")
    print(f"       월출 {hm(g('moonrise'))} · 월남중 {hm(g('moontransit'))} · 월몰 {hm(g('moonset'))}")
    print(f"       시민박명 아침 {hm(g('civilm'))} · 저녁 {hm(g('civils'))}")
    ss, ce = hm(g("sunset")), hm(g("civils"))
    if ss != "-" and ce != "-":
        print(f"\n[사진] 블루아워 {ss} ~ {ce} (일몰 후 하늘 완전히 어두워지기 전)")

if __name__ == "__main__":
    main()
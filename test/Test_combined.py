# -*- coding: utf-8 -*-
"""
오름 1개(랜덤)에 대해 KASI 천문(일출/일몰/월출몰) + KMA 기상(SKY/기온/강수/바람)을
'일몰 시간대' 기준으로 결합해 보여주는 통합 테스트.

실행 전:
  AUTHKEY      = apihub(KMA) authKey
  SERVICE_KEY  = data.go.kr(KASI) '일반 인증키(Decoding)'
실행:  pip install requests  →  python3 test_combined.py
"""
import sqlite3, os, sys, io, math, random
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
import requests

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

AUTHKEY     = "_I4hZYaDRQaOIWWGg2UGjQ"
SERVICE_KEY = "36d19111797705138bf25160862bd78ffe330ee41182657b3fcdafab028a4a8c"

BASE = os.path.dirname(os.path.abspath(__file__))
DB   = os.path.join(BASE, "오름.db")
KMA_URL  = "https://apihub.kma.go.kr/api/typ02/openApi/VilageFcstInfoService_2.0/getVilageFcst"
KASI_URL = "http://apis.data.go.kr/B090041/openapi/service/RiseSetInfoService/getLCRiseSetInfo"

TABLE, COL_NAME, COL_LAT, COL_LON, COL_NX, COL_NY = "오름","오름명","위도","경도","grid_nx","grid_ny"
SLOTS = [2,5,8,11,14,17,20,23]
SKY = {"1":"맑음","3":"구름많음","4":"흐림"}
PTY = {"0":"없음","1":"비","2":"비/눈","3":"눈","4":"소나기"}

# ---------- 공통 ----------
def hm(s):
    s=(s or "").strip()
    return f"{s[:2]}:{s[2:4]}" if len(s)>=4 and s.isdigit() else "-"

def deg16(d):
    dirs=["북","북북동","북동","동북동","동","동남동","남동","남남동",
          "남","남남서","남서","서남서","서","서북서","북서","북북서"]
    return dirs[int((float(d)+11.25)//22.5)%16]

def latlon_to_grid(lat, lon):
    RE,GRID=6371.00877,5.0; SLAT1,SLAT2,OLON,OLAT,XO,YO=30.0,60.0,126.0,38.0,43,136
    D=math.pi/180.0; re=RE/GRID
    sn=math.log(math.cos(SLAT1*D)/math.cos(SLAT2*D))/math.log(
        math.tan(math.pi*0.25+SLAT2*D*0.5)/math.tan(math.pi*0.25+SLAT1*D*0.5))
    sf=math.pow(math.tan(math.pi*0.25+SLAT1*D*0.5),sn)*math.cos(SLAT1*D)/sn
    ro=re*sf/math.pow(math.tan(math.pi*0.25+OLAT*D*0.5),sn)
    ra=re*sf/math.pow(math.tan(math.pi*0.25+lat*D*0.5),sn)
    theta=lon*D-OLON*D; theta=(theta+math.pi)%(2*math.pi)-math.pi; theta*=sn
    return int(ra*math.sin(theta)+XO+0.5), int(ro-ra*math.cos(theta)+YO+0.5)

def latest_base(now):
    t=now-timedelta(minutes=10)
    cand=[s for s in SLOTS if s<=t.hour]
    if cand: return t.strftime("%Y%m%d"), f"{max(cand):02d}00"
    return (t-timedelta(days=1)).strftime("%Y%m%d"), "2300"

# ---------- 데이터 ----------
def pick_oreum():
    con=sqlite3.connect(DB); con.row_factory=sqlite3.Row
    q=(f'SELECT "{COL_NAME}","{COL_LAT}","{COL_LON}","{COL_NX}","{COL_NY}" FROM "{TABLE}" '
       f'WHERE "{COL_LAT}" IS NOT NULL AND "{COL_LAT}" != "" '
       f'AND "{COL_LON}" IS NOT NULL AND "{COL_LON}" != ""')
    rows=con.execute(q).fetchall(); con.close()
    if not rows: sys.exit("좌표 있는 오름 없음.")
    r=random.choice(rows); lat,lon=float(r[COL_LAT]),float(r[COL_LON])
    if r[COL_NX] not in (None,""): nx,ny=int(r[COL_NX]),int(r[COL_NY])
    else: nx,ny=latlon_to_grid(lat,lon)
    return r[COL_NAME],lat,lon,nx,ny

def kasi_times(lat, lon, locdate):
    p={"serviceKey":SERVICE_KEY,"locdate":locdate,"longitude":lon,"latitude":lat,"dnYn":"Y"}
    r=requests.get(KASI_URL,params=p,timeout=15); r.raise_for_status()
    root=ET.fromstring(r.text)
    code=root.findtext(".//resultCode")
    if code not in ("00",None):
        sys.exit(f"KASI 오류: {code} {root.findtext('.//resultMsg') or r.text[:200]}")
    it=root.find(".//item")
    if it is None: sys.exit(f"KASI item 없음:\n{r.text[:300]}")
    g=lambda t: hm(it.findtext(t,""))
    return {k:g(k) for k in ("sunrise","suntransit","sunset","moonrise","moontransit",
                             "moonset","civilm","civils")}

def kma_slots(nx, ny):
    base_date,base_time=latest_base(datetime.now())
    p=dict(pageNo=1,numOfRows=1000,dataType="JSON",base_date=base_date,base_time=base_time,
           nx=nx,ny=ny,authKey=AUTHKEY)
    r=requests.get(KMA_URL,params=p,timeout=15); r.raise_for_status()
    d=r.json(); head=d["response"]["header"]
    if head["resultCode"]!="00":
        sys.exit(f"KMA 오류: {head['resultCode']} {head['resultMsg']}")
    slot={}
    for it in d["response"]["body"]["items"]["item"]:
        slot.setdefault((it["fcstDate"],it["fcstTime"]),{})[it["category"]]=it["fcstValue"]
    return slot, base_date, base_time

def verdict(v):
    노을={"1":"노을·조망 양호","3":"부분 노을 가능","4":"노을 기대 낮음"}.get(v.get("SKY",""),"-")
    out=[노을]
    if v.get("POP") and int(float(v["POP"]))>=40: out.append(f"강수확률 {v['POP']}%↑")
    if v.get("WSD"):
        out.append("바람 강함(촬영 주의)" if float(v["WSD"])>=9 else "바람 약함")
    return " · ".join(out)

# ---------- 메인 ----------
def main():
    if AUTHKEY.startswith("여기에") or SERVICE_KEY.startswith("여기에"):
        sys.exit("AUTHKEY / SERVICE_KEY 둘 다 채워라.")

    name,lat,lon,nx,ny=pick_oreum()
    today=datetime.now().strftime("%Y%m%d")
    astro=kasi_times(lat,lon,today)
    slot,bd,bt=kma_slots(nx,ny)

    print(f"[오름] {name}  ({lat}, {lon})  격자 nx={nx}, ny={ny}")
    print(f"[날짜] {today}   (기상발표 {bd} {bt})\n")

    print(f"[천문] 일출 {astro['sunrise']} · 일몰 {astro['sunset']}"
          f" · 월출 {astro['moonrise']} · 월몰 {astro['moonset']}")
    if astro['civils']!="-":
        print(f"       블루아워(일몰~시민박명끝) {astro['sunset']} ~ {astro['civils']}")

    # 일몰 시간대 기상 (일몰-1h ~ +1h)
    ss=astro['sunset']
    if ss=="-":
        print("\n일몰값 없음 → 기상 매칭 생략"); return
    sh=int(ss[:2]); hours=[f"{h:02d}00" for h in (sh-1,sh,sh+1) if 0<=h<=23]
    snap=f"{round(sh+int(ss[3:])/60):02d}00"          # 일몰 최근접 정시

    print(f"\n[일몰 시간대 기상]  (일몰 {ss} 전후)")
    shown=False
    for t in hours:
        v=slot.get((today,t))
        if not v:
            print(f"  {t[:2]}:{t[2:]}  (예보 없음/지난 시각)"); continue
        shown=True
        mark=" ←일몰" if t==snap else ""
        line=f"  {t[:2]}:{t[2:]}  {SKY.get(v.get('SKY',''),'-')}"
        if "TMP" in v: line+=f" · {v['TMP']}℃"
        if "POP" in v: line+=f" · 강수 {v['POP']}%"
        if "PTY" in v and v['PTY']!="0": line+=f" · {PTY.get(v['PTY'],'')}"
        if "WSD" in v:
            line+=f" · 바람 {v['WSD']}m/s"
            if "VEC" in v: line+=f" {deg16(v['VEC'])}풍"
        print(line+mark)

    sv=slot.get((today,snap))
    if sv:
        print(f"\n[일몰 판정] {verdict(sv)}")
    elif shown:
        print("\n[일몰 판정] 일몰 정시 슬롯이 응답에 없음(지난 시각). 아침 일찍 실행 시 표시됨.")

if __name__ == "__main__":
    main()
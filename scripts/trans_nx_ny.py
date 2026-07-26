# -*- coding: utf-8 -*-
"""
오름_데이터셋.csv 의 (위도, 경도) → 기상청 동네예보 격자번호(nx, ny) 변환 후 컬럼 추가.

- API 키 불필요. 기상청 DFS(Lambert Conformal Conic) 공식으로 오프라인 계산.
- nx·ny는 좌표만으로 결정되는 고정값 → 데이터셋 구축 때 한 번 박아두고 영구 재사용.
- 검증: 서울시청(37.5665,126.9780) → (60,127) 이면 공식 정상.

실행: python3 add_grid_xy.py
"""
import csv, math, os

BASE = os.path.dirname(os.path.abspath(__file__))
CSV  = os.path.join(BASE, "오름_데이터셋.csv")

def latlon_to_grid(lat, lon):
    """위경도 → 동네예보 격자번호 (nx, ny). 기상청 배포 DFS 공식."""
    RE, GRID = 6371.00877, 5.0          # 지구반경(km), 격자간격(km)
    SLAT1, SLAT2 = 30.0, 60.0           # 표준위도
    OLON, OLAT = 126.0, 38.0            # 기준점 경위도
    XO, YO = 43, 136                    # 기준점 격자좌표
    DEGRAD = math.pi / 180.0
    re = RE / GRID
    slat1, slat2 = SLAT1*DEGRAD, SLAT2*DEGRAD
    olon,  olat  = OLON*DEGRAD,  OLAT*DEGRAD
    sn = math.tan(math.pi*0.25 + slat2*0.5) / math.tan(math.pi*0.25 + slat1*0.5)
    sn = math.log(math.cos(slat1)/math.cos(slat2)) / math.log(sn)
    sf = math.tan(math.pi*0.25 + slat1*0.5)
    sf = math.pow(sf, sn) * math.cos(slat1) / sn
    ro = math.tan(math.pi*0.25 + olat*0.5)
    ro = re * sf / math.pow(ro, sn)
    ra = math.tan(math.pi*0.25 + lat*DEGRAD*0.5)
    ra = re * sf / math.pow(ra, sn)
    theta = lon*DEGRAD - olon
    if theta >  math.pi: theta -= 2.0*math.pi
    if theta < -math.pi: theta += 2.0*math.pi
    theta *= sn
    nx = int(ra*math.sin(theta) + XO + 0.5)
    ny = int(ro - ra*math.cos(theta) + YO + 0.5)
    return nx, ny

def main():
    # 공식 자가검증
    assert latlon_to_grid(37.5665, 126.9780) == (60, 127), "DFS 공식 검증 실패"

    rows = list(csv.DictReader(open(CSV, encoding="cp949"), delimiter="\t"))
    cols = list(rows[0].keys())
    for c in ("grid_nx", "grid_ny"):
        if c not in cols:
            cols.append(c)

    done = skip = 0
    for r in rows:
        lat, lon = r.get("위도", "").strip(), r.get("경도", "").strip()
        if lat and lon:
            try:
                nx, ny = latlon_to_grid(float(lat), float(lon))
                r["grid_nx"], r["grid_ny"] = nx, ny
                done += 1
            except ValueError:
                r["grid_nx"] = r["grid_ny"] = ""
                skip += 1
        else:
            r["grid_nx"] = r["grid_ny"] = ""      # 좌표 미확보 행은 공백
            skip += 1

    with open(CSV, "w", encoding="cp949", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, delimiter="\t")
        w.writeheader()
        w.writerows(rows)

    print(f"격자 채움 {done}건 / 좌표없어 건너뜀 {skip}건 → {CSV}")
    # 같은 격자에 묶인 오름 확인(5km 해상도 자연 결과)
    from collections import Counter
    grid = Counter((r["grid_nx"], r["grid_ny"]) for r in rows if r["grid_nx"])
    dup = {k: v for k, v in grid.items() if v > 1}
    if dup:
        print(f"동일 격자 공유 그룹 {len(dup)}개 (예보 동일):")
        for (nx, ny), n in sorted(dup.items(), key=lambda x: -x[1])[:5]:
            names = [r["오름명"] for r in rows if (r["grid_nx"], r["grid_ny"]) == (nx, ny)]
            print(f"   ({nx},{ny}) × {n}: {', '.join(names)}")

if __name__ == "__main__":
    main()
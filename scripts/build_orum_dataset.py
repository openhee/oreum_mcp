# -*- coding: utf-8 -*-
"""
오름 추천 MCP용 데이터셋 빌드 파이프라인
------------------------------------------------------------------
입력
  1) 133개의_오름명소.txt          : 시드(장소만, 지점 제외)
  2) 제주특별자치도_오름현황_*.csv : 표고·비고 (CP949)           [표고/비고 유일 소스]
  3) api_orum.json (선택)          : 제주데이터허브 오름위치 API 덤프 [위경도·카카오url 소스]

출력
  오름_데이터셋.csv
  오름명 | 오름여부 | 지번주소 | 위도 | 경도 | 비고 | 표고 | 카카오맵_url | 관리시설 | 표고비고출처 | 좌표출처

핵심 규칙
  - 조인 키 = 정규화(오름명) + 주소(번지>읍면동)  ← 이름이 유니크하지 않음(민/당/붉은/세미오름 등)
  - 별칭사전(ALIAS)으로 표기차/1:다 수동확정 처리
  - API에는 표고·비고 없음, CSV에는 좌표 없음 → 두 소스를 독립 조인
  - 난이도 계산은 '표고'(해발) 아니라 '비고'(상대높이)를 쓸 것
"""
import re, csv, json, os, glob

# ---------------------------------------------------------------- 경로
BASE          = os.path.dirname(os.path.abspath(__file__))
SEED_TXT      = os.path.join(BASE, "133개의_오름명소.txt")
CSV_GLOB      = os.path.join(BASE, "제주특별자치도_오름현황_*.csv")
API_JSON      = os.path.join(BASE, "api_orum.json")     # 없으면 좌표는 비워둠
OUT_CSV       = os.path.join(BASE, "오름_데이터셋.csv")

# ---------------------------------------------------------------- 별칭사전 (증거기반, 주소로 확정한 것만)
# (시드오름명, 읍면동) -> CSV/‧API에서 실제 매칭할 이름
ALIAS = {
    ("셋개오리",        "봉개동"): "샛개오리",
    ("베릿내오름",      "중문동"): "베릿네오름",
    ("이승이오름",      "남원읍"): "이승악",
    ("금악오름",        "한림읍"): "금오름",
    ("민오름",          "봉개동"): "민오름(무녜오름)",
    ("족은노꼬메오름",  "애월읍"): "노꼬메족은오름",
    ("큰노꼬메오름",    "애월읍"): "노꼬메큰오름",
    ("한라산 웃세족은오름","애월읍"): "웃세오름",
    ("절물오름",        "봉개동"): "절물오름(큰대나)",   # ← 큰대나로 확정 (표고696.9/비고147)
    # 삼형제오름(샛오름)  : 보류 (색달동 남쪽Ⅰ/Ⅱ 택1 미정) → 공백 유지
}

# 비오름 명소(표고·비고 대상 아님). 지점은 시드 파싱 단계에서 이미 제외됨.
NON_OREUM = {"용머리해안", "한라산"}

# ---------------------------------------------------------------- 공통 정규화
def norm(x):
    """공백·'한라산' 접두 제거"""
    return re.sub(r"\s+", "", re.sub(r"^한라산\s*", "", x or ""))

def core(x):
    """괄호 별칭 + 오름/봉/악/산 접미 제거한 핵심어"""
    return re.sub(r"(오름|봉|악|산)$", "", re.sub(r"\(.*?\)", "", norm(x)))

def emd(addr):
    """주소에서 읍/면/동 추출"""
    m = re.findall(r"([가-힣]+(?:읍|면|동))", addr or "")
    return m[0] if m else ""

def bunji(addr):
    """주소 끝 번지(산123-4) 추출 — 동일명 오름 구분용 강한 키"""
    m = re.search(r"(산?\s*\d+(?:-\d+)?)\s*(?:일대|번지)?$", (addr or "").replace(" ", ""))
    return m.group(1) if m else ""

# ---------------------------------------------------------------- 1) 시드 파싱 (장소만)
def load_seed():
    raw = open(SEED_TXT, encoding="utf-8").read()
    seed = []
    for block in raw.split("더보기"):
        lines = [l.strip() for l in block.splitlines() if l.strip()]
        if not lines:
            continue
        kind = lines[0] if lines[0] in ("장소", "지점") else "?"
        rest = lines[1:] if kind != "?" else lines
        name = addr = None
        for l in rest:
            if l.startswith("제주"):
                addr = l
            elif name is None:
                name = l
        if name and kind == "장소":          # ← 지점 제외
            seed.append({"오름명": name, "지번주소": addr or ""})
    return seed

# ---------------------------------------------------------------- 범용 매처
def build_index(rows, name_key):
    """rows -> {정규화명:[row]}, {핵심어:[row]}  (주소 파생키 _emd/_bunji 부착)"""
    by_norm, by_core = {}, {}
    for r in rows:
        r["_emd"]   = emd(r.get("_addr", ""))
        r["_bunji"] = bunji(r.get("_addr", ""))
        by_norm.setdefault(norm(r[name_key]), []).append(r)
        by_core.setdefault(core(r[name_key]), []).append(r)
    return by_norm, by_core

def match(seed_name, seed_addr, by_norm, by_core, by_name):
    """시드 1건 -> 대상 row 1건(+방법) 또는 (None, 사유)"""
    e, bj = emd(seed_addr), bunji(seed_addr)
    if (seed_name, e) in ALIAS:
        tgt = ALIAS[(seed_name, e)]
        if tgt in by_name:
            return by_name[tgt], "별칭"
        # 별칭 대상이 이 소스(예: API)엔 없으면 → 일반 매칭으로 폴백
    cand = by_norm.get(norm(seed_name)) or by_core.get(core(seed_name)) or []
    if not cand:
        return None, "미등재"
    if len(cand) == 1:
        return cand[0], "단일"
    for key, val in (("_bunji", bj), ("_emd", e)):
        f = [c for c in cand if val and c[key] == val]
        if len(f) == 1:
            return f[0], "주소"
    return None, f"모호({len(cand)})"

# ---------------------------------------------------------------- 2) CSV 조인 (표고·비고)
def load_csv_rows():
    path = sorted(glob.glob(CSV_GLOB))[-1]
    rows = list(csv.DictReader(open(path, encoding="cp949")))
    for r in rows:
        r["_addr"] = r["소재지"]
    return rows

def join_csv(seed):
    rows = load_csv_rows()
    by_norm, by_core = build_index(rows, "오름명")
    by_name = {r["오름명"]: r for r in rows}
    hit = 0
    for s in seed:
        if s["오름명"] in NON_OREUM:
            s["비고"] = s["표고"] = ""
            s["표고비고출처"] = "해당없음(비오름)"
            continue
        m, how = match(s["오름명"], s["지번주소"], by_norm, by_core, by_name)
        if m:
            s["비고"], s["표고"] = m["비고"], m["표고"]
            s["표고비고출처"] = f"제주道CSV/{how}"
            hit += 1
        else:
            s["비고"] = s["표고"] = ""
            s["표고비고출처"] = f"미확보/{how}"
    print(f"[CSV] 표고·비고 채움 {hit}/{len(seed)}")
    return seed

# ---------------------------------------------------------------- 3) API 조인 (위경도·카카오url)
def load_api_rows():
    """api_orum.json 로드. 응답봉투{data:[...]} 또는 레코드 리스트 둘 다 허용."""
    if not os.path.exists(API_JSON):
        return None
    obj = json.load(open(API_JSON, encoding="utf-8"))
    recs = obj["data"] if isinstance(obj, dict) and "data" in obj else obj
    for r in recs:
        r["_addr"] = r.get("addressJibun") or r.get("addressDoro") or ""
    return recs

def join_api(seed):
    rows = load_api_rows()
    if rows is None:
        for s in seed:
            s.setdefault("위도", ""); s.setdefault("경도", ""); s.setdefault("카카오맵_url", "")
            s["좌표출처"] = "미확보(api_orum.json 없음)"
        print("[API] api_orum.json 없음 → 위경도·카카오url 스킵")
        return seed
    by_norm, by_core = build_index(rows, "placeName")
    by_name = {r["placeName"]: r for r in rows}
    hit = 0
    for s in seed:
        m, how = match(s["오름명"], s["지번주소"], by_norm, by_core, by_name)
        if m:
            s["위도"]        = m.get("latitude", "")
            s["경도"]        = m.get("longitude", "")
            s["카카오맵_url"] = m.get("placeUrl", "")
            s["좌표출처"]    = f"API/{how}"
            hit += 1
        else:
            s["위도"] = s["경도"] = s["카카오맵_url"] = ""
            s["좌표출처"] = f"미확보/{how}"
            # 별칭 만들기 쉽게: 핵심어가 같은 API 후보 이름 저장
            s["_api_cand"] = [c["placeName"] for c in by_core.get(core(s["오름명"]), [])]
    print(f"[API] 위경도·url 채움 {hit}/{len(seed)}")
    return seed

# ---------------------------------------------------------------- (선택) API 직접 pull
def fetch_api(project_key, save_to=API_JSON):
    """projectKey로 전체 오름을 페이지네이션 수집해 api_orum.json 저장.
       로컬 실행 전용(네트워크 필요). 사용 예: fetch_api('your_key')"""
    import urllib.request
    ENDPOINT = ("https://open.jejudatahub.net/api/proxy/"
                "1Dttb1tab8tD88Dtat11111at1t1atD8/" + project_key)
    out, page = [], 1
    while True:
        url = f"{ENDPOINT}?number={page}&limit=100"
        with urllib.request.urlopen(url, timeout=20) as resp:
            j = json.load(resp)
        out += j.get("data", [])
        if not j.get("hasMore"):
            break
        page += 1
    json.dump(out, open(save_to, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"[API] {len(out)}건 저장 → {save_to}")
    return out

# ---------------------------------------------------------------- 4) 저장
def save(seed):
    cols = ["오름명", "오름여부", "지번주소", "위도", "경도", "비고", "표고",
            "카카오맵_url", "관리시설", "표고비고출처", "좌표출처"]
    for s in seed:
        s["오름여부"] = "명소" if s["오름명"] in NON_OREUM else "오름"
        s.setdefault("관리시설", "")
    with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for s in seed:
            w.writerow({c: s.get(c, "") for c in cols})
    print(f"[저장] {OUT_CSV} ({len(seed)}행)")

# ---------------------------------------------------------------- main
if __name__ == "__main__":
    seed = load_seed()
    print(f"[시드] 장소 {len(seed)}건 (지점 제외)")
    seed = join_csv(seed)
    seed = join_api(seed)
    save(seed)

    # 표고 미확보(수동/제3소스 필요) 리포트
    todo = [s for s in seed if s["오름여부"] == "오름" and not s["표고"]]
    if todo:
        print(f"\n[표고 미확보 {len(todo)}건 — 제3소스/수동]")
        for s in todo:
            print(f"   {s['표고비고출처']:14s} {s['오름명']}  ({s['지번주소']})")

    # 좌표 미확보 리포트 — API 후보 이름까지 보여줌 (별칭 추가용)
    nocoord = [s for s in seed if s["오름여부"] == "오름" and not s["위도"]]
    if nocoord:
        print(f"\n[좌표 미확보 {len(nocoord)}건 — API 매칭 실패]")
        for s in nocoord:
            cand = s.get("_api_cand", [])
            print(f"   {s['좌표출처']:12s} {s['오름명']}  ({emd(s['지번주소'])})  | API후보: {cand}")
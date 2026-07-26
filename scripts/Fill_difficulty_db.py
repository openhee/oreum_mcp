# -*- coding: utf-8 -*-
"""
오름.db 에 난이도 컬럼을 비고(比高) 기반으로 추가/갱신.

추가 컬럼:
  difficulty        INTEGER  1~5 등급
  difficulty_label  TEXT     '아주 쉬움'~'매우 힘듦'
  round_trip_hint   TEXT     참고 왕복시간 범위(넓은 근사)

설계 근거:
  - 웹 실측 왕복시간은 오름당 편차 ±2배(정상왕복 vs 둘레길포함·페이스·다중코스)라
    정밀 분 예측은 근거 부족 → 비고 단독 5등급 + '넓은 참고범위'로만 제공.
  - 게이트(일몰까지 가능?) 판정엔 등급 상단값을 보수적으로 쓰면 충분.

특징: 컬럼 없으면 추가, 있으면 UPDATE (재실행 안전).
실행:  python3 fill_difficulty_db.py
"""
import sqlite3, os, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

BASE = os.path.dirname(os.path.abspath(__file__))
DB   = os.path.join(BASE, "오름.db")
TABLE, COL_NAME, COL_BIGO = "오름", "오름명", "비고"

# (비고 상한, 등급, 라벨, 참고 왕복범위)  — 상한 이하이면 해당 등급
GRADES = [
    (50,    1, "아주 쉬움",  "약 15~30분"),
    (100,   2, "쉬움",      "약 25~45분"),
    (150,   3, "보통",      "약 40~70분"),
    (250,   4, "힘듦",      "약 60~100분"),
    (99999, 5, "매우 힘듦",  "약 90~150분"),
]

def classify(bigo):
    for hi, g, label, hint in GRADES:
        if bigo <= hi:
            return g, label, hint
    return 5, "매우 힘듦", "약 90~150분"

def ensure_column(con, table, col, coltype):
    cols = [r[1] for r in con.execute(f'PRAGMA table_info("{table}")')]
    if col not in cols:
        con.execute(f'ALTER TABLE "{table}" ADD COLUMN "{col}" {coltype}')
        print(f"  + 컬럼 추가: {col} {coltype}")
    else:
        print(f"  = 컬럼 존재: {col} (갱신)")

def main():
    if not os.path.exists(DB):
        sys.exit(f"DB 없음: {DB}")
    con = sqlite3.connect(DB)

    print("[컬럼 준비]")
    ensure_column(con, TABLE, "difficulty",       "INTEGER")
    ensure_column(con, TABLE, "difficulty_label", "TEXT")
    ensure_column(con, TABLE, "round_trip_hint",  "TEXT")

    rows = con.execute(f'SELECT rowid, "{COL_NAME}", "{COL_BIGO}" FROM "{TABLE}"').fetchall()
    done = skipped = 0
    from collections import Counter
    dist = Counter()
    for rowid, name, bigo in rows:
        try:
            b = float(str(bigo).strip())
        except (TypeError, ValueError):
            skipped += 1
            continue
        g, label, hint = classify(b)
        con.execute(
            f'UPDATE "{TABLE}" SET difficulty=?, difficulty_label=?, round_trip_hint=? WHERE rowid=?',
            (g, label, hint, rowid),
        )
        dist[g] += 1
        done += 1

    con.commit()

    print(f"\n[채움] {done}건 / 비고없어 건너뜀 {skipped}건")
    for g in range(1, 6):
        label, hint = next((l, h) for _, gg, l, h in GRADES if gg == g)
        print(f"  등급{g} {label:6s} {hint:11s} → {dist[g]}개")

    # 검증 샘플
    print("\n[검증 샘플]")
    for r in con.execute(
        f'SELECT "{COL_NAME}","{COL_BIGO}",difficulty,difficulty_label,round_trip_hint '
        f'FROM "{TABLE}" ORDER BY CAST("{COL_BIGO}" AS REAL) LIMIT 2'):
        print("  ", r)
    for r in con.execute(
        f'SELECT "{COL_NAME}","{COL_BIGO}",difficulty,difficulty_label,round_trip_hint '
        f'FROM "{TABLE}" ORDER BY CAST("{COL_BIGO}" AS REAL) DESC LIMIT 2'):
        print("  ", r)

    con.close()
    print(f"\n완료 → {DB}")

if __name__ == "__main__":
    main()
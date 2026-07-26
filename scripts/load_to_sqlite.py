# -*- coding: utf-8 -*-
"""
오름_데이터셋.csv -> SQLite 적재
------------------------------------------------------------------
입력  오름_데이터셋.csv (CP949, 탭 구분)
출력  오름.db (테이블: 오름)
"""
import csv
import os
import sqlite3

BASE     = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(BASE, "오름_데이터셋.csv")
DB_PATH  = os.path.join(BASE, "오름.db")

SCHEMA = """
CREATE TABLE 오름 (
    오름명       TEXT NOT NULL,
    오름여부     TEXT,
    지번주소     TEXT,
    위도         REAL,
    경도         REAL,
    비고         REAL,
    표고         REAL,
    카카오맵_url TEXT,
    관리시설     TEXT,
    표고비고출처 TEXT,
    좌표출처     TEXT,
    grid_nx      INTEGER,
    grid_ny      INTEGER,
    PRIMARY KEY (오름명, 지번주소)
)
"""


def to_real(v):
    v = (v or "").strip()
    try:
        return float(v)
    except ValueError:
        return None


def to_int(v):
    v = (v or "").strip()
    try:
        return int(v)
    except ValueError:
        return None


def load_rows():
    with open(CSV_PATH, encoding="cp949") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def load(db_path=DB_PATH):
    rows = load_rows()

    conn = sqlite3.connect(db_path)
    conn.execute("DROP TABLE IF EXISTS 오름")
    conn.execute(SCHEMA)
    conn.executemany(
        """INSERT INTO 오름
               (오름명, 오름여부, 지번주소, 위도, 경도, 비고, 표고,
                카카오맵_url, 관리시설, 표고비고출처, 좌표출처, grid_nx, grid_ny)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                r["오름명"], r["오름여부"], r["지번주소"],
                to_real(r["위도"]), to_real(r["경도"]),
                to_real(r["비고"]), to_real(r["표고"]),
                r["카카오맵_url"], r["관리시설"],
                r["표고비고출처"], r["좌표출처"],
                to_int(r.get("grid_nx", "")), to_int(r.get("grid_ny", "")),
            )
            for r in rows
        ],
    )
    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM 오름").fetchone()[0]
    print(f"[적재] {db_path} 오름 테이블 {n}행")
    conn.close()


if __name__ == "__main__":
    load()

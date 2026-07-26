import sqlite3
import pandas as pd
import os

db_path = "오름.db"
output_dir = "output_csv"

os.makedirs(output_dir, exist_ok=True)

conn = sqlite3.connect(db_path)

tables = pd.read_sql(
    "SELECT name FROM sqlite_master WHERE type='table';",
    conn
)

for table in tables["name"]:
    df = pd.read_sql(f"SELECT * FROM {table}", conn)
    df.to_csv(
        os.path.join(output_dir, f"{table}.csv"),
        index=False,
        encoding="utf-8-sig"
    )

conn.close()

print("완료!")
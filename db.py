import sqlite3
c=sqlite3.connect("오름.db")
print(c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall())
for t in c.execute("SELECT name FROM sqlite_master WHERE type='table'"):
    print(t[0], "→", [r[1] for r in c.execute(f"PRAGMA table_info({t[0]})")])
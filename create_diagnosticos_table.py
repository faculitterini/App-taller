import sqlite3

DB_NAME = "database.db"

con = sqlite3.connect(DB_NAME)
cur = con.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS diagnosticos (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  fecha_mail TEXT,
  from_email TEXT,
  subject TEXT,
  filename TEXT,
  vin TEXT,
  marca TEXT,
  modelo TEXT,
  created_at TEXT,
  sha256 TEXT UNIQUE
);
""")

con.commit()

cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='diagnosticos'")
print("Tabla diagnosticos:", "OK" if cur.fetchone() else "NO")

con.close()

import sqlite3
import sys
sys.path.insert(0, 'D:/OpenClawData/.openclaw/skills/feishu-memory')
from memory import db_conn

conn = db_conn()
c = conn.cursor()
try:
    c.execute('ALTER TABLE decisions ADD COLUMN deadline TEXT')
    conn.commit()
except:
    pass

c.execute("UPDATE decisions SET deadline='2026-05-07' WHERE decision LIKE '%项目D%' AND (deadline IS NULL OR deadline='')")
c.execute("UPDATE decisions SET deadline='2026-05-03' WHERE project='前端框架选型' AND (deadline IS NULL OR deadline='')")
conn.commit()
c.execute("SELECT COUNT(*) FROM decisions WHERE deadline IS NOT NULL AND deadline != ''")
print('Records with deadline:', c.fetchone()[0])
conn.close()

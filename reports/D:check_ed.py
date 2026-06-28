import sqlite3
conn = sqlite3.connect(r'D:\Excavator\Files\excavator_MOEX_DOM.db')
cur = conn.cursor()
cur.execute("SELECT MIN(time), MAX(time), COUNT(*) FROM \"FINAM-AO.ALLFUTED\"")
r = cur.fetchone()
conn.close()

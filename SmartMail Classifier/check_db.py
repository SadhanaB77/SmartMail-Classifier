import sqlite3

conn = sqlite3.connect("database.db")
cursor = conn.cursor()

cursor.execute("""
    SELECT name FROM sqlite_master
    WHERE type='table'
""")

tables = cursor.fetchall()

print("Database tables:", tables)

for table in tables:
    table_name = table[0]
    cursor.execute(f'SELECT COUNT(*) FROM "{table_name}"')
    print(table_name, "records:", cursor.fetchone()[0])

conn.close()
import sqlite3

# 连接到你的数据库文件
conn = sqlite3.connect('data/lottery_data.db')

# 执行 TRUNCATE 检查点操作
# 这会把 wal 文件里的数据全部合并到 db 文件中，并将 wal 文件截断清空
cursor = conn.execute('PRAGMA wal_checkpoint(TRUNCATE);')
print("合并结果:", cursor.fetchone())

conn.close()
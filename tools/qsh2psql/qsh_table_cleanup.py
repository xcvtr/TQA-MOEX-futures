import psycopg2
from psycopg2 import errors

# Настройки подключения к PostgreSQL
DB_CONFIG = {
    "dbname": "fxdom",
    "user": "postgres",
    "password": "ftz951on",
    "host": "wsl",
    "port": "5432"
}

# Функция для подключения к PostgreSQL
def connect_to_db():
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        return conn
    except Exception as e:
        print(f"Ошибка при подключении к PostgreSQL: {e}")
        return None

# Функция для удаления таблиц
def drop_tables(conn):
    try:
        with conn.cursor() as cursor:
            # Удаляем таблицу qsh_orderbook
            cursor.execute("DROP TABLE IF EXISTS qsh_orderbook")
            
            # Удаляем таблицу qsh_trades
            cursor.execute("DROP TABLE IF EXISTS qsh_trades")
            
            # Удаляем таблицу qsh_auxinfo
            cursor.execute("DROP TABLE IF EXISTS qsh_auxinfo")
            
            # Удаляем таблицу qsh_symbols
            cursor.execute("DROP TABLE IF EXISTS qsh_symbols")
            
            conn.commit()
            print("Все таблицы успешно удалены.")
    except Exception as e:
        print(f"Ошибка при удалении таблиц: {e}")
        conn.rollback()

# Основная функция
def main():
    # Подключаемся к PostgreSQL
    conn = connect_to_db()
    if not conn:
        return

    # Удаляем таблицы
    drop_tables(conn)

    # Закрываем соединение
    conn.close()

if __name__ == "__main__":
    main()
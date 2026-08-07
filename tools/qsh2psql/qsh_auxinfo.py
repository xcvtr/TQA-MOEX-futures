import psycopg2
import argparse
from psycopg2 import errors
from datetime import datetime
from psycopg2.extras import execute_values
import time

# Настройки подключения к PostgreSQL
DB_CONFIG = {
    "dbname": "excavator",
    "user": "postgres",
    "password": "ftz951on",
    "host": "localhost",
    "port": "5432"
}

# Максимальное количество попыток
MAX_RETRIES = 30
# Задержка между попытками (в секундах)
RETRY_DELAY = 1

# Функция для подключения к PostgreSQL
def connect_to_db():
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        return conn
    except Exception as e:
        print(f"Ошибка при подключении к PostgreSQL: {e}")
        return None

# Функция для создания таблиц (если они не существуют)
def create_tables(conn):
    try:
        with conn.cursor() as cursor:
            # Создаем таблицу qsh_symbols
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS qsh_symbols (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(50) UNIQUE NOT NULL
                )
            """)

            # Создаем таблицу qsh_auxinfo
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS qsh_auxinfo (
                    id SERIAL,
                    symbol_id SMALLINT REFERENCES qsh_symbols(id),
                    time TIMESTAMPTZ NOT NULL,
                    price DOUBLE PRECISION,
                    ask_total DOUBLE PRECISION,
                    bid_total DOUBLE PRECISION,
                    oi DOUBLE PRECISION,
                    PRIMARY KEY (id, time)
                )
            """)
            try:
                cursor.execute("SELECT create_hypertable('qsh_auxinfo', 'time', if_not_exists => TRUE, create_default_indexes=>TRUE)")
                conn.commit()
            except Exception as e:
                print(f"Ошибка при создании гипертаблицы: {e}")
                conn.rollback()
    except Exception as e:
        print(f"Ошибка при создании таблиц: {e}")
        conn.rollback()


# Функция для получения или создания символа
def get_or_create_symbol(conn, symbol_name):
    with conn.cursor() as cursor:
        # Проверяем, существует ли символ
        cursor.execute("SELECT id FROM qsh_symbols WHERE name = %s", (symbol_name,))
        result = cursor.fetchone()

        if result:
            return result[0]  # Возвращаем существующий id
        else:
            # Создаем новый символ
            cursor.execute("INSERT INTO qsh_symbols (name) VALUES (%s) RETURNING id", (symbol_name,))
            conn.commit()
            return cursor.fetchone()[0]  # Возвращаем новый id

# Функция для парсинга файла
def parse_file(file_path):
    data = []
    with open(file_path, 'r', encoding='utf-8') as file:
        content = file.read()

        # Разделяем файл на строки
        lines = content.splitlines()

        # Извлекаем заголовок
        header = lines[0].strip()
        symbol_name = header.split(':')[1]  # Извлекаем символ (например, CNYRUB_TOM)

        # Обрабатываем строки с данными (начиная со второй строки)
        for line in lines[1:]:
            line = line.strip()
            if not line or line.startswith("Received;ExchTime;Price;AskTotal;BidTotal;OI;HiLimit;LoLimit;Deposit;Rate;Message"):
                continue  # Пропускаем пустые строки и строки с заголовком

            # Разделяем строку на поля
            parts = line.split(';')
            if len(parts) != 11:
                print(f"Ошибка: некорректный формат строки: {line}")
                continue  # Пропускаем некорректные строки

            # Парсим поля
            received_time_str, _, price_str, ask_total_str, bid_total_str, oi_str, _, _, _, _, _ = parts

            try:
                received_time = datetime.strptime(received_time_str, "%d.%m.%Y %H:%M:%S.%f")
                price = float(price_str) if price_str else None
                ask_total = float(ask_total_str) if ask_total_str else None
                bid_total = float(bid_total_str) if bid_total_str else None
                oi = float(oi_str) if oi_str else None
            except ValueError as e:
                print(f"Ошибка при парсинге строки: {line}")
                continue  # Пропускаем некорректные строки

            # Добавляем данные в список
            data.append((symbol_name, received_time, price, ask_total, bid_total, oi))

    return data

def compress_chunks(conn, hypertable_name):
    try:
        with conn.cursor() as cursor:
            # Включаем сжатие для гипертаблицы (если еще не включено)
            cursor.execute(f"""
                ALTER TABLE {hypertable_name} SET (
                    timescaledb.compress,
                    timescaledb.compress_orderby = 'time DESC'
                );
            """)
            conn.commit()

            # Настраиваем политику сжатия (если еще не настроена)
            cursor.execute(f"""
                SELECT add_compression_policy('{hypertable_name}', INTERVAL '7 days', if_not_exists => TRUE);
            """)
            conn.commit()

            # Сжимаем чанки
            cursor.execute(f"""
                DO $$
                DECLARE
                    chunk_name regclass;
                BEGIN
                    FOR chunk_name IN
                        SELECT show_chunks('{hypertable_name}', older_than => INTERVAL '7 days')
                    LOOP
                        PERFORM compress_chunk(chunk_name);
                        RAISE NOTICE 'Сжатие чанка: %', chunk_name;
                    END LOOP;
                END $$;
            """)
            conn.commit()
            #print(f"Сжатие чанков для {hypertable_name} выполнено.")
    except Exception as e:
        print(f"Ошибка при сжатии чанков для {hypertable_name}: {e}")
        conn.rollback()

# Функция для сжатия чанков
def compress_chunks1(conn, hypertable_name):
    try:
        with conn.cursor() as cursor:
            cursor.execute(f"""
                SELECT compress_chunk(i)
                FROM show_chunks('{hypertable_name}')
                WHERE finished = TRUE AND age(now(), _timescaledb_internal.to_timestamp(range_end)) > INTERVAL '7 days';
            """)
            conn.commit()
            print(f"Сжатие чанков для {hypertable_name} выполнено.")
    except Exception as e:
        print(f"Ошибка при сжатии чанков для {hypertable_name}: {e}")
        conn.rollback()

# Функция для сохранения данных в PostgreSQL с повторной попыткой
def save_to_db(conn, data):
    try:
        with conn.cursor() as cursor:
            total_records = len(data)
            batch_size = 1000000  # Размер пакета для вставки

            # Группируем данные по символам
            symbol_data = {}
            for symbol_name, received_time, price, ask_total, bid_total, oi in data:
                if symbol_name not in symbol_data:
                    symbol_data[symbol_name] = []
                symbol_data[symbol_name].append((received_time, price, ask_total, bid_total, oi))

            # Обрабатываем каждый символ
            for symbol_name, records in symbol_data.items():
                # Получаем или создаем символ
                symbol_id = get_or_create_symbol(conn, symbol_name)

                # Подготавливаем данные для вставки
                batch = [(symbol_id, received_time, price, ask_total, bid_total, oi)
                         for received_time, price, ask_total, bid_total, oi in records]

                # Вставляем данные пакетами с повторной попыткой
                for i in range(0, len(batch), batch_size):
                    for attempt in range(MAX_RETRIES):
                        try:
                            execute_values(
                                cursor,
                                """
                                INSERT INTO qsh_auxinfo (symbol_id, time, price, ask_total, bid_total, oi)
                                VALUES %s
                                ON CONFLICT DO NOTHING
                                """,
                                batch[i:i + batch_size],
                                page_size=batch_size
                            )
                            conn.commit()
                            #print(f"Обработано {min(i + batch_size, len(batch))} из {len(batch)} записей для auxinfo {symbol_name}.")
                            break  # Выход из цикла при успешной вставке
                        except errors.UniqueViolation as e:
                            # Обработка нарушения уникальности
                            print(f"Конфликт уникальности: {e}. Попытка {attempt + 1} из {MAX_RETRIES}.")
                            conn.rollback()  # Откат транзакции
                            time.sleep(RETRY_DELAY)  # Задержка перед повторной попыткой
                        except errors.DeadlockDetected as e:
                            # Обработка взаимоблокировки
                            print(f"Обнаружена взаимоблокировка: {e}. Попытка {attempt + 1} из {MAX_RETRIES}.")
                            conn.rollback()  # Откат транзакции
                            time.sleep(RETRY_DELAY)  # Задержка перед повторной попыткой
                        except Exception as e:
                            # Обработка других ошибок
                            print(f"Ошибка: {e}")
                            conn.rollback()  # Откат транзакции
                            break  # Выход из цикла при других ошибках
    except Exception as e:
        print(f"Ошибка при сохранении данных: {e}")
        conn.rollback()
    finally:
        compress_chunks(conn, "qsh_auxinfo")

# Основная функция
def main(file_path):
    # Подключаемся к PostgreSQL
    conn = connect_to_db()
    if not conn:
        return

    # Создаем таблицы (если они не существуют)
    create_tables(conn)

    # Парсим файл
    data = parse_file(file_path)

    # Сохраняем данные в PostgreSQL
    save_to_db(conn, data)

    # Закрываем соединение
    conn.close()

# Точка входа
if __name__ == "__main__":
    # Настройка аргументов командной строки
    parser = argparse.ArgumentParser(description="Обработка файла и сохранение данных в PostgreSQL.")
    parser.add_argument("file_path", type=str, help="Путь к файлу для обработки.")

    # Парсинг аргументов
    args = parser.parse_args()

    # Вызов основной функции с переданным путем к файлу
    main(args.file_path)
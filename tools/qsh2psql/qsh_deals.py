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

# Кодировка типов заявок
ORDER_TYPES = {
    "Buy": 2,  # Покупка
    "Sell": 1  # Продажа
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

def create_tables(conn):
    try:
        with conn.cursor() as cursor:
            # Создаем таблицу qsh_symbols (если она не существует)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS qsh_symbols (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(50) UNIQUE NOT NULL
                )
            """)
            #print("Таблица qsh_symbols создана или уже существует.")

            # Создаем таблицу qsh_trades (если она не существует)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS qsh_trades (
                    id SERIAL,
                    symbol_id SMALLINT REFERENCES qsh_symbols(id),
                    time TIMESTAMPTZ NOT NULL,
                    exch_time TIMESTAMPTZ NOT NULL,
                    deal_id BIGINT,
                    type INTEGER,
                    price DOUBLE PRECISION,
                    volume DOUBLE PRECISION,
                    oi DOUBLE PRECISION,
                    PRIMARY KEY (id, time)
                )
            """)
            #print("Таблица qsh_trades создана или уже существует.")

            # Проверяем, является ли qsh_trades уже гипертаблицей
            cursor.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1
                        FROM _timescaledb_catalog.hypertable
                        WHERE table_name = 'qsh_trades'
                    ) THEN
                        PERFORM create_hypertable('qsh_trades', 'time', if_not_exists => TRUE);
                    END IF;
                END $$;
            """)
            conn.commit()
            #print("Гипертаблица qsh_trades создана или уже существует.")

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
            if not line or line.startswith("Received;ExchTime;DealId;Type;Price;Volume;OI"):
                continue  # Пропускаем пустые строки и строку с заголовком

            # Разделяем строку на поля
            parts = line.split(';')
            if len(parts) != 7:
                print(f"Ошибка: некорректный формат строки: {line}")
                continue  # Пропускаем некорректные строки

            # Парсим поля
            received_time_str, exch_time_str, deal_id_str, order_type_str, price_str, volume_str, oi_str = parts

            try:
                received_time = datetime.strptime(received_time_str, "%d.%m.%Y %H:%M:%S.%f")
                exch_time = datetime.strptime(exch_time_str, "%d.%m.%Y %H:%M:%S.%f")
                deal_id = int(deal_id_str)
                order_type = ORDER_TYPES.get(order_type_str)  # Кодируем тип заявки
                price = float(price_str)
                volume = float(volume_str)
                oi = float(oi_str)
            except (ValueError, KeyError) as e:
                print(f"Ошибка при парсинге строки: {line}")
                continue  # Пропускаем некорректные строки

            # Добавляем данные в список
            data.append((symbol_name, received_time, exch_time, deal_id, order_type, price, volume, oi))

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
            try:
                with conn.cursor() as cursor:
                    cursor.execute(f"""
                        DO $$
                        DECLARE
                            chunk_name regclass;
                        BEGIN
                            FOR chunk_name IN
                                SELECT show_chunks('{hypertable_name}', older_than => INTERVAL '7 days')
                            LOOP
                                BEGIN
                                    PERFORM compress_chunk(chunk_name);
                                    RAISE NOTICE 'Сжатие чанка: %', chunk_name;
                                EXCEPTION
                                    WHEN OTHERS THEN
                                        RAISE NOTICE 'Ошибка при сжатии чанка %: %', chunk_name, SQLERRM;
                                END;
                            END LOOP;
                        END $$;
                    """)
                    conn.commit()
                    #print(f"Сжатие чанков для {hypertable_name} выполнено.")
            except Exception as e:
                print(f"Ошибка при сжатии чанков для {hypertable_name}: {e}")
                conn.rollback()
            #print(f"Сжатие чанков для {hypertable_name} выполнено.")
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
            for symbol_name, received_time, exch_time, deal_id, order_type, price, volume, oi in data:
                if symbol_name not in symbol_data:
                    symbol_data[symbol_name] = []
                symbol_data[symbol_name].append((received_time, exch_time, deal_id, order_type, price, volume, oi))

            # Обрабатываем каждый символ
            for symbol_name, records in symbol_data.items():
                # Получаем или создаем символ
                symbol_id = get_or_create_symbol(conn, symbol_name)

                # Подготавливаем данные для вставки
                batch = [(symbol_id, received_time, exch_time, deal_id, order_type, price, volume, oi)
                         for received_time, exch_time, deal_id, order_type, price, volume, oi in records]

                # Вставляем данные пакетами с повторной попыткой
                for i in range(0, len(batch), batch_size):
                    for attempt in range(MAX_RETRIES):
                        try:
                            execute_values(
                                cursor,
                                """
                                INSERT INTO qsh_trades (symbol_id, time, exch_time, deal_id, type, price, volume, oi)
                                VALUES %s
                                ON CONFLICT DO NOTHING
                                """,
                                batch[i:i + batch_size],
                                page_size=batch_size
                            )
                            conn.commit()
                            #print(f"Обработано {min(i + batch_size, len(batch))} из {len(batch)} записей для deals {symbol_name}.")
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
        compress_chunks(conn, 'qsh_trades')

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
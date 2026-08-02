#!/usr/bin/env python3 -u
"""Update PG go with КСУР ПГО from MOEX XML + FINAM XLS (пониженное ГО).

Формула (КВАЛ с услугой «Пониженное ГО»):
    GO = initial_margin(ближний контракт) × kpur / ksur
где:
    kpur = КВАЛ-ставка (колонка 3, «повышенный уровень риска»)
    ksur = станд-ставка (колонка 12, «стандартный уровень риска»)
    kpur/ksur ≈ 0.51-0.56 (понижение ~45-49%)

Тикеры без услуги ПГО (нет в XLS) — GO = биржевое initial_margin (без понижения).

Usage: python3 update_go_ksur_pgo.py
Источники:
    MOEX:  https://www.moex.com/export/derivatives/go.aspx
    FINAM: https://www.finam.ru/files/u/dw/files/commissionrates/marjsettings/instrumenty__edp_YYYY-MM-DD.xlsx
"""
import re, subprocess, urllib.request, psycopg2, openpyxl
from datetime import datetime, date
from pathlib import Path

# ── MAP: наш тикер → (MOEX prefix, XLS код базового актива | None если нет ПГО) ──
MAP = {
    'Si':   ('Si',    'USD_FUT'),
    'GZ':   ('GAZR',  'GAZP_FUT'),
    'GD':   ('GOLD',  'GLDRU'),
    'MM':   ('MXI',   'MIX'),
    'NG':   ('NG',    'NGASRU'),
    'RN':   ('ROSN',  'ROSN_FUT'),
    'CR':   ('CNY',   'CNY_FUT'),
    'SV':   ('SILV',  'SILVRU'),
    'BR':   ('BR',    None),   # Brent нет в XLS ПГО → биржевое ГО
}

XLS_URL = 'https://www.finam.ru/files/u/dw/files/commissionrates/marjsettings/instrumenty__edp_{date}.xlsx'
XLS_CACHE = Path('/home/user/.hermes/document_cache')

def fetch_moex_go():
    """MOEX go.xml → {prefix: [(delivery_date, code, im, medium), ...]}

    im = initial_margin (ГО high-risk), medium = buy_deposit_medium_risk (ГО КСУР).
    """
    req = urllib.request.Request('https://www.moex.com/export/derivatives/go.aspx',
                                 headers={'User-Agent': 'Mozilla/5.0'})
    content = urllib.request.urlopen(req, timeout=30).read().decode('cp1251')
    today = date.today()
    moex = {}
    for item in re.findall(r'<item[^>]*/>', content):
        c = re.search(r'code="([^"]+)"', item)
        im = re.search(r'initial_margin="([\d.]+)"', item)
        med = re.search(r'buy_deposit_medium_risk="([\d.]+)"', item)
        dd = re.search(r'delivery_date="([\d\s]+)"', item)
        if not (c and im and med and dd): continue
        code = c.group(1)
        prefix = code.split('-')[0]
        try:
            d = datetime.strptime(dd.group(1).strip(), '%Y%m%d').date()
        except: continue
        if (d - today).days >= 30:  # ближайшие к экспирации, но не просроченные
            moex.setdefault(prefix, []).append((d, code, float(im.group(1)), float(med.group(1))))
    return moex

def fetch_finam_xls():
    """Скачать XLS пониженного ГО с ФИНАМ (актуальная дата), вернуть {code: (kpur, ksur)}."""
    for delta in range(0, 7):  # пробуем сегодня, вчера, ... (7 дней назад)
        d = (date.today() - __import__('datetime').timedelta(days=delta)).isoformat()
        url = XLS_URL.format(date=d)
        path = XLS_CACHE / f'instrumenty__edp_{d}.xlsx'
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0.0.0'})
            data = urllib.request.urlopen(req, timeout=30).read()
            if len(data) < 10000: continue  # не XLSX (HTML-заглушка)
            path.write_bytes(data)
            print(f'XLS: {path.name} ({len(data)} байт)')
            break
        except Exception as e:
            print(f'XLS {d}: {e}')
    else:
        raise RuntimeError('Не удалось скачать XLS ФИНАМ')

    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb['Фьючерсы ФОРТС с пониженным ГО']
    rates = {}
    for row in range(7, ws.max_row + 1):
        code = ws.cell(row=row, column=2).value
        kpur = ws.cell(row=row, column=3).value
        ksur = ws.cell(row=row, column=12).value
        if code and kpur is not None and ksur is not None:
            rates[str(code)] = (float(kpur), float(ksur))
    return rates

def main():
    moex = fetch_moex_go()
    finam = fetch_finam_xls()

    conn = psycopg2.connect(host='10.0.0.60', port=5432, dbname='moex', user='postgres')
    cur = conn.cursor()

    print(f'{"Ticker":5s} {"Front":12s} {"im":>8s} {"kpur":>6s} {"ksur":>6s} {"ratio":>7s} {"go_new":>8s} {"go_old":>8s}')
    print('-' * 80)
    for ticker, (mpref, xkey) in MAP.items():
        cts = sorted(moex.get(mpref, []))
        if not cts:
            print(f'{ticker:5s} NO MOEX DATA'); continue
        d, code, im, med = cts[0]  # ближайший контракт

        if xkey is None:
            # нет услуги ПГО (BR) → medium ГО (КСУР без понижения)
            go_new = round(med, 0)
            kpur = ksur = None
        else:
            if xkey not in finam:
                print(f'{ticker:5s} {code:12s} {med:>8.0f} NO XLS RATE ({xkey})'); continue
            kpur, ksur = finam[xkey]
            # GO = medium (MOEX КСУР) × ставка_КСУР_уменьшения (XLS кол12)
            go_new = round(med * ksur, 0)

        cur.execute("SELECT go FROM futures.ticker_specs WHERE ticker = %s", (ticker,))
        row = cur.fetchone()
        go_old = float(row[0]) if row else 0
        ratio = f'{kpur/ksur:.4f}' if kpur else '-'
        print(f'{ticker:5s} {code:12s} {med:>8.0f} {kpur if kpur else 0:>6} {ksur if ksur else 0:>6} {ratio:>7s} {go_new:>8.0f} {go_old:>8.0f}')
        cur.execute("UPDATE futures.ticker_specs SET go = %s WHERE ticker = %s", (go_new, ticker))

    conn.commit()
    cur.close(); conn.close()
    print('\n✅ PG updated.')

if __name__ == '__main__':
    main()

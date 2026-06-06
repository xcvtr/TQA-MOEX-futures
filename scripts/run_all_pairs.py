#!/home/user/venvs/tqa/main/bin/python
"""
Batch run pair_dashboard.py for all 13 symbols — PARALLEL.

Usage:
  python scripts/run_all_pairs.py [--start 2025-01-01] [--end 2026-05-31]
                                  [--workers 8] [--sym audjpy,eurusd]
                                  [--output-dir /path]

Changes from v1 (sequential):
  v1: 13 symbols × 40-90s = 10-15 min
  v2: 8 workers parallel = 2-3 min
"""
import argparse, os, signal, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

SYMBOLS = [
    'audjpy', 'audusd', 'euraud', 'eurgbp', 'eurjpy', 'eurusd',
    'gbpjpy', 'gbpusd', 'nzdusd', 'usdcad', 'usdchf', 'usdjpy',
    'xauusd',
]

# ─── Track active processes for graceful shutdown ───
_active_procs: list[dict] = []
_shutdown_flag = False


def _shutdown_handler(signum, frame):
    """SIGINT/SIGTERM — kill all children, then exit."""
    global _shutdown_flag
    if _shutdown_flag:
        return  # already handling
    _shutdown_flag = True
    print("\n\n⚠️  Получен сигнал остановки. Завершаю процессы...")
    _kill_children()
    sys.exit(130)


def _kill_children():    
    # Step 1: SIGTERM to each process group
    for proc_info in _active_procs:
        pid = proc_info.get('pid')
        if pid and pid > 0:
            try:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
    
    # Step 2: wait 5 seconds, then SIGKILL survivors
    deadline = time.monotonic() + 5
    still_alive = []
    while time.monotonic() < deadline:
        still_alive = [p for p in _active_procs if p.get('proc') and p['proc'].poll() is None]
        if not still_alive:
            break
        time.sleep(0.5)
    
    for proc_info in still_alive:
        pid = proc_info.get('pid')
        if pid and pid > 0:
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass


signal.signal(signal.SIGINT, _shutdown_handler)
signal.signal(signal.SIGTERM, _shutdown_handler)


def run_symbol(sym: str, script: str, python: str, args: list[str],
               timeout: int) -> dict:
    """Run pair_dashboard.py for one symbol. Returns result dict."""
    if _shutdown_flag:
        return {'symbol': sym, 'status': 'CANCELLED', 'time': 0, 'pid': None,
                'error': 'Shutdown in progress'}
    
    cmd = [python, script, '--sym', sym] + args
    
    for attempt in range(1, 3):  # max 2 attempts
        if _shutdown_flag:
            return {'symbol': sym, 'status': 'CANCELLED', 'time': 0,
                    'pid': None, 'error': 'Shutdown in progress'}
        
        t0 = time.monotonic()
        attempt_label = f" (попытка {attempt}/2)" if attempt > 1 else ""
        
        proc = None
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                preexec_fn=os.setsid,  # isolate process group for killpg
            )
            pid = proc.pid
            _active_procs.append({'proc': proc, 'pid': pid, 'sym': sym})
            
            if not _shutdown_flag:
                attempt_str = f"[retry #{attempt}] " if attempt > 1 else ""
                print(f"  {attempt_str}{sym.upper():8s} ⏳ PID {pid}{attempt_label}", flush=True)
            
            try:
                stdout, stderr = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                # Kill timed-out process
                try:
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    proc.kill()
                proc.wait(timeout=10)
                
                elapsed = time.monotonic() - t0
                if attempt == 1:
                    print(f"  {sym.upper():8s} 🔴 TIMEOUT ({elapsed:.0f}s) → retry #1", flush=True)
                    time.sleep(2)
                    continue
                else:
                    _remove_proc(pid)
                    return {'symbol': sym, 'status': 'TIMEOUT', 'time': elapsed,
                            'pid': pid, 'error': f'Превышен лимит {timeout}s x2'}
            
            elapsed = time.monotonic() - t0
            _remove_proc(pid)
            
            if proc.returncode == 0:
                print(f"  {sym.upper():8s} ✅ {elapsed:.1f}s", flush=True)
                return {'symbol': sym, 'status': 'OK', 'time': elapsed, 'pid': pid}
            else:
                err_text = stderr.strip()[-200:] if stderr.strip() else 'Нет stderr'
                error_str = f"exit code {proc.returncode}: {err_text}"
                
                if attempt == 1:
                    print(f"  {sym.upper():8s} 🔴 {error_str} → retry #1", flush=True)
                    time.sleep(2)
                    continue
                else:
                    return {'symbol': sym, 'status': 'FAIL', 'time': elapsed,
                            'pid': pid, 'error': error_str}
        
        except FileNotFoundError:
            return {'symbol': sym, 'status': 'FAIL', 'time': 0, 'pid': None,
                    'error': f'Файл не найден: {script}'}
        except Exception as e:
            _pid = proc.pid if proc else None
            _remove_proc(_pid)
            if attempt == 1:
                print(f"  {sym.upper():8s} 🔴 {e} → retry #1", flush=True)
                time.sleep(2)
                continue
            return {'symbol': sym, 'status': 'FAIL', 'time': time.monotonic() - t0,
                    'pid': _pid, 'error': str(e)}
    
    return {'symbol': sym, 'status': 'FAIL', 'time': 0, 'pid': None, 'error': 'Unknown'}


def _remove_proc(pid):
    global _active_procs
    if pid is None:
        return
    _active_procs = [p for p in _active_procs if p.get('pid') != pid]


def print_summary(results: list[dict], wall_clock: float):
    """Print summary table."""
    total = len(results)
    ok_count = sum(1 for r in results if r['status'] == 'OK')
    fail_count = sum(1 for r in results if r['status'] in ('FAIL', 'TIMEOUT'))
    cancel_count = sum(1 for r in results if r['status'] == 'CANCELLED')
    total_time = sum(r['time'] for r in results if r['time'])
    
    print()
    print("═" * 70)
    print("  📊 СВОДКА")
    print("═" * 70)
    
    # Header
    print(f"  {'Symbol':>10s} │ {'Status':12s} │ {'Time':>7s} │ Error")
    print(f"  {'─'*10}─┼─{'─'*12}─┼─{'─'*7}─┼─{'─'*30}")
    
    for r in results:
        sym = r['symbol'].upper()
        status = r['status']
        t = f"{r['time']:.1f}s" if r['time'] else '—'
        err = (r.get('error') or '')[:40]
        
        icon = {'OK': '✅', 'FAIL': '🔴', 'TIMEOUT': '⏰', 'CANCELLED': '⛔'}.get(status, '❓')
        status_str = f"{icon} {status}"
        
        print(f"  {sym:>10s} │ {status_str:12s} │ {t:>7s} │ {err}")
    
    print(f"  {'─'*10}─┼─{'─'*12}─┼─{'─'*7}─┼─{'─'*30}")
    
    pct = ok_count / total * 100 if total else 0
    print(f"  {'ИТОГО':>10s} │ {ok_count}/{total} ✅ ({pct:.0f}%) │ Σ {total_time:.0f}s │ Стена: {wall_clock:.1f}s")
    
    if cancel_count:
        print(f"  Отменено: {cancel_count}")
    print()


def main():
    parser = argparse.ArgumentParser(
        description='Batch run pair_dashboard.py for all symbols — parallel')
    parser.add_argument('--start', default='2025-01-01', help='Start date')
    parser.add_argument('--end', default='2026-05-31', help='End date')
    parser.add_argument('--workers', type=int, default=8,
                        help='Parallel workers (default: 8)')
    parser.add_argument('--sym', help='Comma-separated symbols (default: all 13)')
    parser.add_argument('--timeout', type=int, default=180,
                        help='Timeout per attempt in seconds (default: 180)')
    
    args = parser.parse_args()
    
    # Resolve symbols
    if args.sym:
        symbols = [s.strip().lower() for s in args.sym.split(',')]
        unknown = [s for s in symbols if s not in SYMBOLS]
        if unknown:
            print(f"❌ Неизвестные символы: {', '.join(unknown)}")
            print(f"   Допустимые: {', '.join(SYMBOLS)}")
            sys.exit(1)
    else:
        symbols = list(SYMBOLS)
    
    n = len(symbols)
    workers = min(args.workers, n)
    
    script = Path(__file__).parent / 'pair_dashboard.py'
    
    # Use the python from pair_dashboard.py's shebang, not current venv
    with open(script) as f:
        shebang = f.readline().strip()
    python = shebang[2:] if shebang.startswith('#!') else sys.executable
    
    pair_args = ['--start', args.start, '--end', args.end]
    
    print(f"📊 Запуск {n} символов ({args.start} → {args.end})")
    print(f"   Workers: {workers} | Таймаут: {args.timeout}s/попытка | Retry: 1")
    print(f"   Скрипт: {script}")
    print(f"   Python: {python}")
    print("═" * 70)
    
    wall_t0 = time.monotonic()
    results = []
    
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            fut_map = {
                pool.submit(
                    run_symbol, sym, str(script), python, pair_args, args.timeout
                ): sym for sym in symbols
            }
            
            for future in as_completed(fut_map):
                sym = fut_map[future]
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    results.append({
                        'symbol': sym, 'status': 'FAIL', 'time': 0,
                        'pid': None, 'error': str(e),
                    })
    except KeyboardInterrupt:
        # _shutdown_handler already ran, just cleanup
        _kill_children()
    
    wall_elapsed = time.monotonic() - wall_t0
    
    # Sort results by original symbol order
    sym_order = {s: i for i, s in enumerate(symbols)}
    results.sort(key=lambda r: sym_order.get(r.get('symbol', '').lower(), 999))
    
    print_summary(results, wall_elapsed)
    
    # Exit code
    failed = any(r['status'] in ('FAIL', 'TIMEOUT') for r in results)
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()

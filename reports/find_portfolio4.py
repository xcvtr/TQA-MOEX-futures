import subprocess

# List all files/dirs in /home/user
ls = subprocess.run(['ls', '-1', '/home/user/'], capture_output=True, text=True, timeout=5)
all_items = ls.stdout.strip().split('\n')
print(f"Total items in /home/user: {len(all_items)}")

# Find py files with trading logic
for f in all_items:
    if f.endswith('.py') and ('btest' in f or 'signal' in f or 'backtest' in f or 'strategy' in f or 'portfolio' in f or 'trade' in f):
        print(f"TRADING PY: {f}")

# Check subdirectories for portfolio.py
for f in all_items:
    if f.startswith('.') or '.' in f:
        continue
    full = f'/home/user/{f}'
    if subprocess.run(['test', '-d', full]).returncode == 0:
        subls = subprocess.run(['ls', '-1', full], capture_output=True, text=True, timeout=3)
        for sf in subls.stdout.strip().split('\n'):
            if 'portfolio' in sf:
                print(f"FOUND: {full}/{sf}")

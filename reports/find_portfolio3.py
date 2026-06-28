import subprocess

# Just search for portfolio.py in /home/user
ls = subprocess.run(['ls', '-1', '/home/user/'], capture_output=True, text=True, timeout=5)
all_files = ls.stdout.strip().split('\n')
for f in all_files:
    if 'portfolio' in f:
        print(f"PORTFOLIO: {f}")

# Also check for py files that might be the portfolio
for f in all_files:
    if 'portfolio' in f.lower():
        print(f"MATCH: {f}")

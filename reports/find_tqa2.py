import subprocess

# Quick check - just look at home dir top level
ls = subprocess.run(['ls', '-1', '/home/user/'], capture_output=True, text=True, timeout=5)
print("HOME contents (first 40):")
for f in ls.stdout.strip().split('\n')[:40]:
    if 'portfolio' in f or 'tqa' in f.lower() or 'moex' in f.lower() or 'trade' in f.lower():
        print(f"  *** {f}")
    else:
        print(f"  {f}")

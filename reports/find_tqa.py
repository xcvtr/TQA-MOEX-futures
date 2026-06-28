import subprocess

# Search for portfolio.py in specific likely directories
dirs = [
    '/home/user/projects/tqa-moex',
    '/home/user/tqa-moex',
    '/home/user/tqa',
    '/home/user/TQA',
    '/home/user/Projects',
    '/home/user/projects',
]

for d in dirs:
    if subprocess.run(['test', '-d', d]).returncode == 0:
        print(f"EXISTS: {d}")
        ls = subprocess.run(['ls', d], capture_output=True, text=True)
        print(f"  contents: {ls.stdout[:200]}")
        pf = subprocess.run(['find', d, '-name', 'portfolio.py', '-maxdepth', '5'], 
                          capture_output=True, text=True)
        if pf.stdout.strip():
            print(f"  portfolio.py: {pf.stdout.strip()}")
    else:
        print(f"NOT FOUND: {d}")

# Also look at home dir top level
ls_home = subprocess.run(['ls', '-la', '/home/user/'], capture_output=True, text=True)
print("\nHOME top level:")
for line in ls_home.stdout.split('\n')[:30]:
    print(line)

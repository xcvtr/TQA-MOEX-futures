import os, subprocess

# Check current directory first
print("CWD:", os.getcwd())
ls = subprocess.run(['ls', '-la'], capture_output=True, text=True, cwd='/home/user/.hermes/hermes-agent')
print("CWD contents:", ls.stdout[:500])

# Check a few specific places
for d in ['/home/user/.hermes/hermes-agent', '/home/user', '/home/user/projects']:
    if os.path.isdir(d):
        try:
            for item in os.listdir(d):
                fp = os.path.join(d, item)
                if os.path.isfile(fp) and item == 'portfolio.py':
                    print(f"FOUND: {fp}")
                if os.path.isdir(fp) and item == 'tqa':
                    print(f"TQA DIR: {fp}")
                    for sub in os.listdir(fp):
                        if 'portfolio' in sub:
                            print(f"  SUB: {os.path.join(fp, sub)}")
        except PermissionError:
            print(f"Permission denied: {d}")

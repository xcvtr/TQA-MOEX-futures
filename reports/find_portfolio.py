import os
import subprocess

result = subprocess.run(['find', '/home/user', '-name', 'portfolio.py', '-type', 'f'], 
                       capture_output=True, text=True, timeout=10)
print("FIND RESULT:", result.stdout.strip())
print("STDERR:", result.stderr.strip())

# Also check common locations
for d in ['/home/user', '/home/user/.hermes', '/home/user/projects']:
    if os.path.isdir(d):
        for root, dirs, files in os.walk(d):
            for f in files:
                if f == 'portfolio.py':
                    print(f"FOUND: {os.path.join(root, f)}")
            # Don't go too deep
            if root.count(os.sep) > 8:
                break

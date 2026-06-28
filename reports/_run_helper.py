import subprocess, os
res = subprocess.run([
    '/bin/bash', '/home/user/run_matryoshka.sh'
], capture_output=True, text=True, timeout=300, cwd='/home/user/projects/TQA-crypto')
out = res.stdout[-10000:] if len(res.stdout) > 10000 else res.stdout
print('=== STDOUT ===')
print(out)
err = res.stderr[:3000] if res.stderr else ''
if err:
    print('=== STDERR ===')
    print(err)
print('=== RC:', res.returncode)

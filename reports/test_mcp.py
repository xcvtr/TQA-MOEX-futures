import os, subprocess, json, sys

env = os.environ.copy()
env['PYTHONPATH'] = '/home/user/projects/TQA/services/SCOPE/backend'
server_path = '/home/user/projects/TQA/services/MCP/tqa_mcp_server.py'
python_path = '/home/user/venvs/tqa/main/bin/python3'

proc = subprocess.Popen([python_path, server_path],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, text=True, env=env)

def send_req(msg):
    proc.stdin.write(json.dumps(msg) + '\n')
    proc.stdin.flush()
    line = proc.stdout.readline()
    if not line:
        err = proc.stderr.read()
        proc.terminate()
        proc.wait()
        raise RuntimeError(f'MCP died: {err}')
    return json.loads(line.strip())

# initialize
init = {"jsonrpc":"2.0","id":1,"method":"initialize",
        "params":{"protocolVersion":"2024-11-05",
                  "capabilities":{"roots":{"listChanged":True},"sampling":{}},
                  "clientInfo":{"name":"test","version":"0.1"}})
resp = send_req(init)
print('init:', resp)

# call get_dom_positions with minimal args
call = {"jsonrpc":"2.0","id":2,"method":"get_dom_positions",
        "params":{"symbol":"AUDJPY","start":"2025-01-01T00:00:00","end":"2025-01-02T00:00:00"}}
resp2 = send_req(call)
print('response:', json.dumps(resp2, indent=2))

proc.terminate()
proc.wait()
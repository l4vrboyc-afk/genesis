"""Verify daytrader profile boots correctly: port 8003 + MT5 connected."""
import os, socket, time, sys, signal, subprocess, urllib.request, json
from pathlib import Path

os.chdir(Path(__file__).resolve().parent.parent)
os.environ["GENESIS_PROFILE"] = "daytrader"
os.environ["GENESIS_LAUNCHED_BY"] = "gui"

# Kill stale
result = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, check=True)
for line in result.stdout.splitlines():
    for port in ["8000", "8003"]:
        if f":{port}" in line and "LISTENING" in line:
            pid = line.split()[-1]
            if pid.isdigit() and int(pid) != os.getpid():
                subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True, timeout=5)

proc = subprocess.Popen(
    [sys.executable, "main.py"],
    cwd=str(Path.cwd()),
    env=os.environ,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
)
print(f"Spawned pid={proc.pid}")

# Poll for port + status
end = time.monotonic() + 35
port_ok = False
status = None

while time.monotonic() < end:
    if not port_ok:
        s = socket.socket()
        if s.connect_ex(("127.0.0.1", 8003)) == 0:
            port_ok = True
            print(f"Port 8003: UP at {time.monotonic() - (end - 35):.2f}s")
        s.close()
    if port_ok:
        try:
            resp = urllib.request.urlopen("http://127.0.0.1:8003/api/status", timeout=3)
            status = json.loads(resp.read().decode())
            if status.get("mt5_connected"):
                print(f"\n{'='*50}\n✅ DAYTRADER: ALL SYSTEMS OK\n{'='*50}")
                print(f"  Port: 8003, MT5: connected, Balance: ${status['balance']}")
                print(f"  Profile: {status['active_profile']}, Equity: ${status['equity']}")
                break
        except Exception:
            pass
    time.sleep(0.5)

if status:
    print(f"\nFinal status at {time.monotonic() - (end - 35):.2f}s:")
    print(f"  mt5_connected: {status.get('mt5_connected')}")
    print(f"  balance: ${status.get('balance')}")
    print(f"  equity: ${status.get('equity')}")
    print(f"  active_profile: {status.get('active_profile')}")
else:
    print(f"\nTimeout after 35s - port={port_ok}")
    with open("logs/daytrader.log") as f:
        lines = f.read().splitlines()
    for l in lines[-5:]:
        print(f"  LOG: {l}")

# Kill
if sys.platform == "win32":
    proc.send_signal(signal.CTRL_BREAK_EVENT)
else:
    proc.send_signal(signal.SIGINT)
try:
    proc.wait(timeout=5)
except:
    proc.kill()
print("Done")

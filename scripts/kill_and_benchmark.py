"""
Kill all existing Genesis/Python processes, then run the profile benchmark.
"""
import subprocess, time, os, socket, sys

os.chdir(r"C:\Users\Moses Egbunike\Documents\Claude Code Projects\Genesis")

# Kill ALL python.exe processes (except this one)
result = subprocess.run(
    ["wmic", "process", "where", 'name="python.exe"', "get", "processid"],
    capture_output=True, text=True, timeout=10
)

my_pid = str(os.getpid())
killed = 0
for line in result.stdout.splitlines():
    pid = line.strip()
    if pid and pid.isdigit() and pid != my_pid:
        subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True, timeout=5)
        killed += 1
        time.sleep(0.3)

print(f"Killed {killed} Python process(es)")
time.sleep(2)

# Verify ports free
for port in [8000, 8001, 8002, 8003]:
    s = socket.socket()
    rc = s.connect_ex(("127.0.0.1", port))
    s.close()
    print(f"Port {port}: {'FREE' if rc != 0 else 'STILL LISTENING!'}")

# Run the benchmark
print("\n" + "=" * 62)
print("  Starting Benchmark...")
print("=" * 62)
sys.stdout.flush()

sys.path.insert(0, os.getcwd())
import scripts.benchmark_profiles as bp
bp.main()

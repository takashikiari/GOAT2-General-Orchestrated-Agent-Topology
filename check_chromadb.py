import subprocess, sys

print("=== Attempt 1: curl http://localhost:8000/api/v1/tenants ===")
try:
    r = subprocess.run(["curl", "-s", "--connect-timeout", "5", "http://localhost:8000/api/v1/tenants"],
                       capture_output=True, text=True, timeout=10)
    out = r.stdout + r.stderr
    print(out if out.strip() else "(no output)")
    print(f"return code: {r.returncode}")
except Exception as e:
    print(f"Error: {e}")

print()
print("=== Attempt 2: netstat -tlnp | grep 8000 ===")
try:
    r = subprocess.run(["netstat", "-tlnp"], capture_output=True, text=True, timeout=10)
    lines = [l for l in r.stdout.split("\n") if "8000" in l]
    if lines:
        print("\n".join(lines))
    else:
        print("(no lines matching port 8000)")
    if r.stderr.strip():
        print(f"stderr: {r.stderr}")
except Exception as e:
    print(f"Error: {e}")

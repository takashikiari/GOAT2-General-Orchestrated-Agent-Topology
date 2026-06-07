import subprocess, sys, os

os.chdir("/home/lenovo/workspace/goat2")
result = subprocess.run(
    [sys.executable, "redis_memory_check.py"],
    capture_output=True,
    text=True,
    timeout=30
)
print(result.stdout)
if result.stderr:
    print("STDERR:", result.stderr)
print("EXIT CODE:", result.returncode)

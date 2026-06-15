import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
modules = ["screen", "input_control", "shell", "browser", "clipboard"]
errors = []
for m in modules:
    try:
        __import__(f"goat_skills.{m}")
        print(f"OK: goat_skills.{m}")
    except Exception as e:
        errors.append((m, str(e)))
        print(f"FAIL: goat_skills.{m} -> {e}")
if errors:
    print(f"\n{len(errors)} FAILED: {[e[0] for e in errors]}")
else:
    print(f"\nAll {len(modules)} OK")
sys.exit(1 if errors else 0)

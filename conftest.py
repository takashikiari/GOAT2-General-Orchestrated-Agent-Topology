"""pytest bootstrap: sys.path fix + test/production log isolation.

IMPORTANT: the os.environ assignment must come before any import that
calls get_logger(), because utils.logging.setup installs the
RotatingFileHandler on first import (module-level, not deferred).
Setting GOAT_LOG_DIR here redirects the file handler to a test-only
path so the production log (default /tmp/goat2/logs/goat2.log) is
never touched by pytest runs.  Test log output is still written to
/tmp/pytest-goat2/logs/goat2.log for post-run inspection, and pytest's
own log-capture system remains fully active (warnings/errors appear in
the pytest report on failure, unaffected by which file handler is used).
"""
import os
import sys
from pathlib import Path

# Redirect before any import that could trigger get_logger().
os.environ["GOAT_LOG_DIR"] = "/tmp/pytest-goat2/logs"

sys.path.insert(0, str(Path(__file__).resolve().parent))
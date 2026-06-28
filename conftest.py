"""pytest bootstrap: ensure the project root is on sys.path so tests can use the
same absolute imports the app uses (``from orchestrator...`` etc.)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
"""``python -m mcp_server`` entry point — delegates to ``server.main``.

The canonical entry is ``python -m mcp_server.server`` (so
``argparse``'s ``--help`` shows the right program name), but
running the bare package also works for convenience.
"""
from mcp_server.server import main

if __name__ == "__main__":
    main()
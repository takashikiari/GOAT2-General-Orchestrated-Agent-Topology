"""
tools — GOAT's tool package.

Each module builds one or more ``ToolDefinition`` instances (from
``orchestrator.tools``) bound to the services they need. Import the tool you
want directly::

    from tools.memory_tools import build_search_memory_tool
"""
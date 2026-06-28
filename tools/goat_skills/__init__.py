"""tools.goat_skills — hot-reloadable orchestrator tool plugins.

Each ``*.py`` here (except this ``__init__.py``) is a plugin that exposes
``build(registry) -> list[ToolDefinition]``. Loaded and watched by
``plugins.plugin_manager.PluginManager``.
"""
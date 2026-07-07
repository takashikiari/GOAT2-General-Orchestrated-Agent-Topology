"""Shared fakes for orchestrator memory-flow tests (no real backends)."""
from __future__ import annotations

import asyncio


class _Message:
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _Choice:
    def __init__(self, message):
        self.message = message


class _Response:
    def __init__(self, content="ok"):
        self.choices = [_Choice(_Message(content=content))]


class _Completions:
    def __init__(self, content="ok", delay=0.0):
        self._content = content
        self._delay = delay

    async def create(self, **kw):
        if self._delay:
            await asyncio.sleep(self._delay)
        return _Response(self._content)


class _Chat:
    def __init__(self, completions):
        self.completions = completions


class _LLMClient:
    def __init__(self, completions):
        self.chat = _Chat(completions)


class _FakeLayers:
    def __init__(self, results=None, blocks=None, l3_used=0):
        self._results = results or []
        self._blocks = blocks or ["[Identity]\nYou are GOAT."]
        self._l3_used = l3_used
        self.search_calls = 0
        self.last_query = None
        self.archive_calls = 0

    async def search_episodic_with_cache(self, chat_id, query, limit=5, topic_id=None, chat_id_filter=None):
        self.search_calls += 1
        self.last_query = query
        return list(self._results), False, "search:deadbeef"

    async def search_episodic(self, query, limit=5, after=None, before=None, topic_id=None, chat_id_filter=None):
        self.search_calls += 1
        return list(self._results)

    async def find_by_keys(self, chat_id, keys, limit=15):
        return list(self._results)

    async def get_identity_and_facts(self):
        return {}

    async def get_identity_prompt(self):
        from memory.config import IDENTITY_BASE_PROMPT
        return IDENTITY_BASE_PROMPT

    async def bump_access(self, chat_id, ids):
        return None

    async def assemble_context(self, chat_id, budget=None, l3_results=None,
                               facts=None, messages=None, identity_prompt=None):
        return list(self._blocks), self._l3_used

    async def get_working_context(self, chat_id):
        return []

    async def save_working_context(self, chat_id, messages):
        self.saved = messages

    async def append_and_save_working_context(self, chat_id, *messages_to_append):
        existing = getattr(self, "saved", []) or []
        self.saved = list(existing) + list(messages_to_append)

    async def store_episodic(self, chat_id: str, content: str, tags=None, topic_id: str = "", doc_id: str | None = None) -> str:
        self.archive_calls += 1
        return doc_id or str(__import__("uuid").uuid4())

    # L2.5 activation layer — fakes return empty/None so every single-turn test
    # sees a COLD turn (no prior activation, no embedding) and the existing
    # behaviour (search runs, cache_key reported) is preserved.
    async def get_activation(self, chat_id):
        return None

    async def set_activation(self, chat_id, activation):
        self.set_activation_calls = getattr(self, "set_activation_calls", 0) + 1

    async def clear_activation(self, chat_id):
        pass

    async def embed_query(self, query):
        return None

    async def boost_by_entities(self, query, results):
        return results


class _FakeAnalytics:
    def __init__(self):
        self.total_requests = 0
        self.records = []

    def record(self, obs):
        self.records.append(obs)
        self.total_requests += 1

    def log_report(self):
        pass


class _FakePluginManager:
    tools = []


class _FakeRegistry:
    def __init__(self, layers, llm, analytics):
        self.memory_layers = layers
        self.llm_client = llm
        self.memory_analytics = analytics
        self.plugin_manager = _FakePluginManager()
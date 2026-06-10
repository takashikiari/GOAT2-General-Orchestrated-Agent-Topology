"""Prompt templates for GOAT 2.0 agents.

This module exports system prompts used by different agent types.
Each prompt is designed for specific agent roles and their execution patterns.

Exports:
    RESEARCHER_SYSTEM: System prompt for ResearcherAgent (deep research).
"""
from __future__ import annotations

from agents.prompts.researcher_prompt import _SYSTEM_PROMPT as RESEARCHER_SYSTEM

__all__ = ["RESEARCHER_SYSTEM"]
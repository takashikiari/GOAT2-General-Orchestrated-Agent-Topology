"""Backward compatibility shim for memory.working_record.

This module has been moved to memory.working.working_record.
"""
from memory.working.working_record import RecordDict

__all__ = ["RecordDict"]
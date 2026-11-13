"""Compatibility imports; use the supported top-level storage APIs instead.

Experimental backend flags and context variables have been replaced by the
public storage_policy protocols and current_borrow_purpose().
"""
from .slot_identity import slot_identity
from .storage_relocation import relocated_slot

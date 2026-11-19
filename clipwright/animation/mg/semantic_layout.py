"""Deprecated aesthetic checks.

Aesthetic judgment (contrast, units, bbox, card labels, flow spacing) is owned by
the vision inspection tool and the animation Agent. This module intentionally
returns no failures so pipeline hardcodes cannot override the Agent.

Schema/type/size hard contracts remain in ``validator.py``.
"""
from __future__ import annotations

from typing import Any


def semantic_layout_errors(mg_def: dict[str, Any], params: dict[str, Any] | None = None) -> list[str]:
    return []


def semantic_layout_failures(mg_def: dict[str, Any], params: dict[str, Any] | None = None) -> list[Any]:
    return []

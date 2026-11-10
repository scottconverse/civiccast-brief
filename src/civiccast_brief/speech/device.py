"""Which torch device the speaker splitter runs on. Copied from textflowkit's engine.py."""

from __future__ import annotations


def pick_device(prefer: str | None = None) -> str:
    """The device named, else cuda when torch sees one, else cpu."""
    if prefer:
        return prefer
    try:
        import torch
    except ImportError:
        return "cpu"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"

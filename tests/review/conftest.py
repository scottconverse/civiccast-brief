"""Shared desk test data: a small article in the writer's shape."""

from __future__ import annotations

import copy

import pytest

ARTICLE = {
    "headline": "Council passes the budget",
    "dek": "A 6-1 vote after two hours",
    "sections": [
        {"heading": "The vote", "paragraphs": [{"text": "It passed 6-1.", "cites": ["c1"]}]}
    ],
    "votes": [{"motion": "Adopt the budget", "result": "passed", "yes": 6, "no": 1}],
    "quotes": [{"speaker": "Mayor", "text": "A good budget.", "cite": "c2"}],
    "topics": ["budget"],
}


@pytest.fixture
def article() -> dict:
    return copy.deepcopy(ARTICLE)

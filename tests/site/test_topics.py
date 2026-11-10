"""Topics and boards: keyword mapping onto the fixed list, the Government fallback, and slugs."""

from __future__ import annotations

import dataclasses

import pytest
from site_support import FIXTURES

from civiccast_brief.site.model import load_site_input
from civiccast_brief.site.topics import TOPIC_BY_SLUG, TOPICS, slugify, topics_for


def _council():  # type: ignore[no-untyped-def]
    return load_site_input(FIXTURES).episodes[0]


def test_fixture_episode_maps_onto_the_fixed_list_in_order() -> None:
    slugs = [topic.slug for topic in topics_for(_council())]
    assert "land-use" in slugs  # "annexation"
    assert "transit" in slugs  # "traffic"
    order = [topic.slug for topic in TOPICS]
    assert slugs == sorted(slugs, key=order.index)


def test_episode_with_no_matching_words_falls_back_to_government() -> None:
    episode = dataclasses.replace(_council(), claims=(), tags=(), article=None)
    assert [topic.slug for topic in topics_for(episode)] == ["government"]


def test_keywords_match_at_word_starts_only() -> None:
    episode = dataclasses.replace(
        _council(), claims=(), article=None, tags=("Copper Peak rezoning",)
    )
    assert "land-use" in [topic.slug for topic in topics_for(episode)]
    # "plat" must not match inside "template".
    episode = dataclasses.replace(_council(), claims=(), article=None, tags=("template",))
    assert [topic.slug for topic in topics_for(episode)] == ["government"]


def test_topic_slugs_are_unique_and_known() -> None:
    assert len(TOPIC_BY_SLUG) == len(TOPICS)
    assert "government" in TOPIC_BY_SLUG
    for topic in TOPICS:
        assert slugify(topic.slug) == topic.slug
        assert topic.short and topic.name


@pytest.mark.parametrize(
    ("text", "slug"),
    [
        ("City Council", "city-council"),
        ("Planning & Zoning Commission", "planning-zoning-commission"),
        ("  Board of Adjustment!  ", "board-of-adjustment"),
        ("***", "board"),
    ],
)
def test_slugify(text: str, slug: str) -> None:
    assert slugify(text) == slug

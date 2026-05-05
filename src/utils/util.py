"""Shared utilities for the crawler: content hashing, scrapper type registry."""

import hashlib
import json
from enum import Enum
from typing import Any


class ScrapperType(str, Enum):
    """Canonical scraper identifiers.

    Values must stay in sync with the keys in ``src.scrapers.SCRAPER_REGISTRY``
    and with the ``scrapper_type`` column stored in ``scraped_items``.
    """

    BELLATRIX_UPDATES = "bellatrix_updates"
    DIGANTARA_NEWSROOM = "digantara_newsroom"
    NSIL_NEWS = "nsil_news"
    PIXXEL_NEWSROOM = "pixxel_newsroom"
    SATSURE_NEWSROOM = "satsure_newsroom"
    SKYROOT_NEWSROOM = "skyroot_newsroom"
    X_LATEST_POSTS = "x_latest_posts"


# Fields that form the canonical identity of a scraped item for hashing.
_HASH_FIELDS = ("url",)


def item_hashcode(item: dict[str, Any]) -> str:
    """Return a SHA-256 hex digest of the canonical content fields of *item*.

    Only the fields listed in ``_HASH_FIELDS`` contribute to the hash so that
    internal bookkeeping fields (ids, timestamps, run references) do not affect
    deduplication logic.
    """
    content = {k: item.get(k, "") for k in _HASH_FIELDS}
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


def scrapper_type_from_item_id(item_id: str) -> ScrapperType | None:
    """Infer the scrapper type from the *item_id* prefix where unambiguous.

    Currently only ``tweet:<id>`` maps deterministically to a scrapper type.
    All other prefixes (``news:``, ``listing:``, ``press_release:``, …) are
    shared across multiple scrapers and require the source record to resolve;
    in those cases ``None`` is returned.
    """
    if item_id.startswith("tweet:"):
        return ScrapperType.X_LATEST_POSTS
    return None

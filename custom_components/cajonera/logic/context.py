"""What "he cogido 3" refers to after a search (spec section 4.3: the LAST part searched).

Every search replaces the context. A search that does not point at exactly one
part clears it, so a later "he cogido 3" can never fall back on an older part.
"""

from __future__ import annotations

from datetime import datetime

from .search import SearchResult
from .withdraw import Context


def context_after_search(result: SearchResult, now: datetime) -> Context | None:
    if len(result.part_pks) != 1:
        return None
    drawers = {h.location.pk for h in result.hits if h.part.pk == result.part_pks[0]}
    location = next(iter(drawers)) if len(drawers) == 1 else None
    return Context(result.part_pks[0], location, now)

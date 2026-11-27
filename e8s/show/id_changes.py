"""
id_changes.py --- reports the set and spot ID renames between two shows

Sets are matched by storage key, which never changes: a set whose key is in both shows and whose
ID differs was renamed. Spots have no stable key, so a rename is inferred: a spot that vanished
and a spot that appeared are one rename when their placements are equal in every set, every role
value but the spot ID itself. Anything else is a removal and an addition, which a host that
follows spots across a rename-and-move sees until the format gains stable spot keys.

Contains:
    id_changes(): lists the set renames and the inferred spot renames between two shows
"""

import json
from collections import Counter
from collections.abc import Mapping

from e8s.core.canonical import canonical_value
from e8s.core.ids import SetId, SpotId
from e8s.show.change_models import IdChanges, ItemChange
from e8s.show.item_table import ID_FIELD
from e8s.show.items import changes
from e8s.show.models import Show

type _Signature = tuple[tuple[str, str, str], ...]


def _set_renames(found: tuple[ItemChange, ...]) -> tuple[tuple[SetId, SetId], ...]:
    pairs: set[tuple[SetId, SetId]] = set()
    for change in found:
        if change.key.kind != "set" or change.key.role is not None:
            continue
        if isinstance(change.before, Mapping) and isinstance(change.after, Mapping):
            was, now = change.before[ID_FIELD], change.after[ID_FIELD]
            if was != now:
                pairs.add((SetId(str(was)), SetId(str(now))))
    return tuple(sorted(pairs))


def _signatures(found: tuple[ItemChange, ...], *, is_removal: bool) -> dict[SpotId, _Signature]:
    """Collects, per vanished (or new) spot, its placement role values in every set."""
    spots = {
        SpotId(change.key.path[0])
        for change in found
        if change.key.kind == "spot"
        and (change.after is None if is_removal else change.before is None)
    }
    rows: dict[SpotId, list[tuple[str, str, str]]] = {spot: [] for spot in spots}
    for change in found:
        key = change.key
        value = change.before if is_removal else change.after
        if key.kind != "placement" or value is None or SpotId(key.path[1]) not in spots:
            continue
        text = json.dumps(canonical_value(value), sort_keys=True)
        rows[SpotId(key.path[1])].append((key.path[0], key.role or "", text))
    return {spot: tuple(sorted(row)) for spot, row in rows.items()}


def _spot_renames(found: tuple[ItemChange, ...]) -> tuple[tuple[SpotId, SpotId], ...]:
    gone = _signatures(found, is_removal=True)
    new = _signatures(found, is_removal=False)
    gone_counts, new_counts = Counter(gone.values()), Counter(new.values())
    by_signature = {signature: spot for spot, signature in new.items()}
    pairs = [
        (spot, by_signature[signature])
        for spot, signature in gone.items()
        if signature and gone_counts[signature] == 1 and new_counts[signature] == 1
    ]
    return tuple(sorted(pairs))


def id_changes(before: Show, after: Show) -> IdChanges:
    """Lists the set renames and the inferred spot renames between two shows.

    Set renames are exact, matched by storage key. A spot rename is reported when one spot
    vanished and one appeared with equal placements in every set; a rename that also moves
    the spot is a removal and an addition, so it is not reported. With several spots renamed
    at once, a pair is reported only when its placements match exactly one vanished spot and
    exactly one new spot; spots with identical placements (stacked spots) are never paired, and
    neither is a spot with no placements. Both lists are sorted and cost follows what changed.

    Args:
        before: The older show.
        after: The newer show.

    Returns:
        renames: Set renames and spot renames as (old, new) pairs.

    >>> from e8s.cast.spots import rename_spots
    >>> from e8s.examples import two_set_show
    >>> show = two_set_show()
    >>> id_changes(show, rename_spots(show, {"T1": "S1"})[0]).spots
    (('T1', 'S1'),)
    """
    found = changes(before, after).changes
    return IdChanges(sets=_set_renames(found), spots=_spot_renames(found))

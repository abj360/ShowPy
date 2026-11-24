"""
storable.py --- refuses a show whose IDs, keys, paths, forms or pins the stored layout cannot hold

Contains:
    require_storable(): refuses a show whose set IDs, keys, paths, forms or pins cannot be stored
"""

from collections import Counter

from e8s.core.errors import SchemaError
from e8s.show.document_paths import is_storable_folder
from e8s.show.manage import SET_ID_PATTERN
from e8s.show.models import Show
from e8s.show.set_keys import set_key


def require_storable(show: Show) -> None:
    """Refuses a show the stored layout cannot hold: bad IDs or keys, loose paths or forms, repeats.

    >>> from e8s.examples import two_set_show
    >>> require_storable(two_set_show())
    """
    bad: list[str] = [s for s in show.set_ids() if not SET_ID_PATTERN.fullmatch(s) or ".." in s]
    bad += [set_key(s) for s in show.sets if not is_storable_folder(set_key(s))]
    pairs = set(show.transition_ids())
    loose = [spec.id for spec in show.transitions if spec.id not in pairs]
    orphans = [f for f, form in show.forms.items() if form.set not in show.set_ids()]
    if bad or loose or orphans:
        raise SchemaError(
            "show cannot be stored",
            hint="use set ids of letters, digits, _ and .; store paths only for consecutive "
            "sets; give every form an existing set",
            context={"bad_set_ids": bad, "loose_transitions": loose, "orphan_forms": orphans},
        )
    pins = Counter((p.target, p.id) for p in show.pins)
    specs = Counter(str(spec.id) for spec in show.transitions)
    repeated_pins = sorted([*key] for key, n in pins.items() if n > 1)
    repeated_specs = sorted(tid for tid, n in specs.items() if n > 1)
    if repeated_pins or repeated_specs:
        raise SchemaError(
            "show repeats a pin or a stored path spec, which one stored record cannot hold",
            hint="re-pin a repeated pin with pin(show, target, id), which keeps one pin per target"
            " and ID; store a repeated pair's paths with store_paths(show, PathsOp(spec=...)),"
            " which keeps one spec per pair",
            context={"repeated_pins": repeated_pins, "repeated_transitions": repeated_specs},
        )

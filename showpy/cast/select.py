"""
select.py --- the selection language plus spatial picks, ordering, splitting and mirror pairing

Contains:
    Selection: re-exports the ordered, duplicate-free ID set from showpy.core.selection
    Selector: re-exports the alias for anything that picks spots
    SelectorAst: holds a parsed selector as a postfix program of words and operators
    parse_selector(): parses selector text, refusing `&` mixed with `+` or `-` at one level
    select(): resolves an expression, ID list or Selection against a show, set or cast
    is_label_dependent(): reports whether an expression names spots by their labels
    label_prefix(): returns the part of a label before its trailing number
    in_region(): picks on-field spots inside a ring or polygon, boundary included
    near(): picks on-field spots within a radius of a point
    where(): picks spots whose placement passes a predicate
    ordered_along(): orders spots by form order, x, y or position along a curve
    split(): cuts a selection into k consecutive near-equal groups
    alternate(): deals a selection into k interleaved groups
    mirror_pairs(): pairs spots with their mirror partners across a vertical axis
"""

import math
import re
from collections.abc import Callable, Iterable, Sequence
from typing import TYPE_CHECKING, Literal, NamedTuple

import numpy as np
import shapely
from scipy.optimize import linear_sum_assignment

from showpy.cast.models import Cast
from showpy.choreo.models import Placement, Set
from showpy.core.caps import MAX_DEPTH, MAX_RANGE, MAX_TEXT_CHARS, require_size
from showpy.core.error_codes import ErrorCode
from showpy.core.errors import GeometryError, SchemaError, UnknownRefError
from showpy.core.ids import FormId, SpotId
from showpy.core.selection import Selection as Selection
from showpy.core.selection import Selector as Selector
from showpy.core.selection import operand_ids
from showpy.core.types import Meters, Point, Points, Region
from showpy.core.util import (
    RANGE_PATTERN,
    close_matches,
    natural_sort,
    parse_range,
    split_id,
)
from showpy.geo.curve_base import Curve
from showpy.geo.regions import Area, region

if TYPE_CHECKING:
    from showpy.show.models import Show

TOKEN_PATTERN = re.compile(r"[+\-&()]|[^\s+&()\-][^\s+&()]*")
VALID_FORMS = (
    "use T4, T1-T8, T*, all, group:<key>, form:<id>, tag:<tag>, shape:circle|rectangle or "
    "sel:<name>, joined by + (union), - (difference), & (intersection) and parentheses; "
    "put spaces or parentheses around operators (T1 + T2 - T3); T2-T3 without spaces is a range"
)
OPERATORS = ("+", "-", "&")
ALL_WORD = "all"
PREFIX_MARK = "*"
KIND_SEPARATOR = ":"
SHAPE_KINDS = ("circle", "rectangle")
SPOT_KINDS = ("group", "tag", "shape", "sel")
AXIS_EPS = 1e-9

type OrderBy = Literal["form", "x", "y"] | Curve
type RegionLike = Area | Region | Points | Sequence[Point]


class _Token(NamedTuple):
    text: str
    position: int

    @property
    def is_word(self) -> bool:
        return self.text not in (*OPERATORS, "(", ")")


class _Universe(NamedTuple):
    """Holds what an expression can resolve against; None marks data the source lacks."""

    ids: tuple[SpotId, ...]
    source: str
    cast: Cast | None
    forms: dict[str, tuple[SpotId, ...]] | None


def _bad_token(message: str, position: int, expr: str) -> SchemaError:
    return SchemaError(
        message, hint=VALID_FORMS, context={"position": position, "expression": expr}
    )


def _glued(expr: str, position: int) -> bool:
    neighbours = (expr[position - 1 : position], expr[position + 1 : position + 2])
    return any(c and not c.isspace() and c not in "()" for c in neighbours)


def _tokenize(expr: str) -> list[_Token]:
    require_size(len(expr), MAX_TEXT_CHARS, what="expression characters")
    tokens = [_Token(match[0], match.start()) for match in TOKEN_PATTERN.finditer(expr)]
    for token in tokens:
        if token.text in OPERATORS and _glued(expr, token.position):
            raise _bad_token(
                f"operator {token.text!r} must be separated by spaces or parentheses",
                token.position,
                expr,
            )
    return tokens


def _set_forms(set_: Set) -> dict[str, tuple[SpotId, ...]]:
    members: dict[FormId, list[Placement]] = {}
    for placement in set_.placements:
        if placement.form is not None:
            members.setdefault(placement.form, []).append(placement)
    return {
        form: tuple(p.entity for p in sorted(rows, key=_order_key))
        for form, rows in members.items()
    }


def _order_key(placement: Placement) -> float:
    return math.inf if placement.order is None else placement.order


def _universe(source: "Show | Set | Cast") -> _Universe:
    if isinstance(source, Cast):
        return _Universe(source.ids(), "cast", source, None)
    if isinstance(source, Set):
        return _Universe(source.entity_ids(), "set", None, _set_forms(source))
    forms: dict[str, tuple[SpotId, ...]] = {
        form_id: form.members for form_id, form in source.forms.items()
    }
    return _Universe(source.cast.ids(), "show", source.cast, forms)


def _unknown(kind: str, name: str, known: Sequence[str], token: _Token) -> UnknownRefError:
    close = close_matches(name, known)
    return UnknownRefError(
        f"unknown {kind} {name!r}",
        hint=f"did you mean {', '.join(close)}?" if close else f"check the {kind} name",
        context={"position": token.position, kind: name},
    )


def label_prefix(spot_id: str) -> str | None:
    """Returns the part of a label before its trailing number, or None without a number.

    >>> label_prefix("flag-12"), label_prefix("lead")
    ('flag-', None)
    """
    prefix, number = split_id(spot_id)
    return None if number is None else prefix


def _resolve_prefix(universe: _Universe, token: _Token) -> tuple[SpotId, ...]:
    prefix = token.text[: -len(PREFIX_MARK)]
    found = tuple(i for i in universe.ids if label_prefix(i) == prefix)
    if not found:
        known = sorted({p for p in map(label_prefix, universe.ids) if p is not None})
        raise _unknown("prefix", prefix, known, token)
    return found


def _resolve_range(universe: _Universe, token: _Token) -> tuple[SpotId, ...]:
    expanded = parse_range(token.text)
    present: dict[str, SpotId] = {spot_id: spot_id for spot_id in universe.ids}
    for endpoint in (expanded[0], expanded[-1]):
        if endpoint not in present:
            raise _unknown("spot", endpoint, universe.ids, token)
    return tuple(present[i] for i in expanded if i in present)


def _resolve_form(universe: _Universe, name: str, token: _Token) -> tuple[SpotId, ...]:
    if universe.forms is None:
        raise SchemaError(
            "form: needs a Show or Set",
            hint="pass the Show (form member order) or a Set (placement order)",
            context={"position": token.position},
        )
    if name not in universe.forms:
        raise _unknown("form", name, list(universe.forms), token)
    return universe.forms[name]


def _require_cast(universe: _Universe, kind: str, token: _Token) -> Cast:
    if universe.cast is None:
        raise SchemaError(
            f"{kind}: needs the cast, which a Set does not hold",
            hint="pass the Show or its Cast to select by group, tag, shape or sel",
            context={"position": token.position},
        )
    return universe.cast


def _resolve_spot_kind(cast: Cast, kind: str, name: str, token: _Token) -> tuple[SpotId, ...]:
    if kind == "group":
        if name not in cast.groups:
            raise _unknown("group", name, list(cast.groups), token)
        return tuple(s.id for s in cast.spots if s.group == name)
    if kind == "tag":
        found = tuple(s.id for s in cast.spots if name in s.tags)
        if not found:
            raise _unknown("tag", name, sorted({t for s in cast.spots for t in s.tags}), token)
        return found
    if kind == "shape":
        if name not in SHAPE_KINDS:
            raise SchemaError(
                f"unknown shape {name!r}",
                hint="use shape:circle or shape:rectangle",
                context={"position": token.position},
            )
        return tuple(s.id for s in cast.spots if s.shape.kind == name)
    if name not in cast.selections:
        raise _unknown("selection", name, list(cast.selections), token)
    members = set(cast.selections[name])
    return tuple(i for i in cast.ids() if i in members)


def _resolve_kind(universe: _Universe, token: _Token, expr: str) -> tuple[SpotId, ...]:
    kind, _, name = token.text.partition(KIND_SEPARATOR)
    if kind == "form":
        return _resolve_form(universe, name, token)
    if kind not in SPOT_KINDS:
        raise _bad_token(f"unknown selector kind {kind!r}", token.position, expr)
    return _resolve_spot_kind(_require_cast(universe, kind, token), kind, name, token)


def _resolve_word(universe: _Universe, token: _Token, expr: str) -> tuple[SpotId, ...]:
    text = token.text
    if text == ALL_WORD:
        found = universe.ids
    elif KIND_SEPARATOR in text:
        found = _resolve_kind(universe, token, expr)
    elif text.endswith(PREFIX_MARK):
        found = _resolve_prefix(universe, token)
    elif text in universe.ids:
        found = (universe.ids[universe.ids.index(text)],)
    elif RANGE_PATTERN.match(text):
        found = _resolve_range(universe, token)
    else:
        raise _unknown("spot", text, universe.ids, token)
    return found


class _Level(NamedTuple):
    """Tracks one parenthesis level by token index: where it opened and its terms began."""

    opened: int
    first: int | None = None
    last: int = 0
    kind: str | None = None
    pending: _Token | None = None


class SelectorAst(NamedTuple):
    """Holds a parsed selector as a postfix program that a stack evaluates without recursion.

    Attributes:
        text: The expression it was parsed from.
        program: Words and operators in postfix order; an operator combines the two results
            before it, left operand first.
    """

    text: str
    program: tuple[_Token, ...]


class _Parser:
    """Turns tokens into a postfix program one token at a time, with an explicit level stack.

    `+` and `-` combine left to right, `&` chains with itself, and a level that mixes `&` with
    `+` or `-` is refused.

    Attributes:
        tokens: Tokens of the expression.
        expr: The original text, for error context.
        program: Postfix program built so far.
        levels: Open parenthesis levels, outermost first.
    """

    def __init__(self, tokens: list[_Token], expr: str) -> None:
        self.tokens = tokens
        self.expr = expr
        self.program: list[_Token] = []
        self.levels = [_Level(opened=-1)]

    def parse(self) -> SelectorAst:
        """Returns the postfix program of the whole expression, or raises at the first error."""
        wants_term = True
        for index, token in enumerate(self.tokens):
            if wants_term:
                wants_term = self._term_token(index, token)
            else:
                wants_term = self._after_term(index, token)
        end = len(self.expr)
        if wants_term:
            raise _bad_token("expression ended, expected a spot, group or (", end, self.expr)
        if len(self.levels) > 1:
            raise _bad_token("expression ended, expected )", end, self.expr)
        return SelectorAst(self.expr, tuple(self.program))

    def _term_token(self, index: int, token: _Token) -> bool:
        if token.is_word:
            self.program.append(token)
            self._close_term(index)
            return False
        if token.text != "(":
            raise _bad_token(f"unexpected {token.text!r}", token.position, self.expr)
        require_size(len(self.levels), MAX_DEPTH, what="nesting depth")
        self.levels.append(_Level(opened=index))
        return True

    def _close_term(self, start: int) -> None:
        level = self.levels[-1]
        if level.pending is not None:
            self.program.append(level.pending)
        first = start if level.first is None else level.first
        self.levels[-1] = level._replace(first=first, last=start, pending=None)

    def _after_term(self, index: int, token: _Token) -> bool:
        if token.text == ")" and len(self.levels) > 1:
            self._close_term(self.levels.pop().opened)
            return False
        if token.text not in OPERATORS:
            raise _bad_token(f"expected an operator, got {token.text!r}", token.position, self.expr)
        level = self.levels[-1]
        kind = token.text if token.text == "&" else "+-"
        if level.kind not in (None, kind):
            raise self._mixed(index, level)
        self.levels[-1] = level._replace(kind=kind, pending=token)
        return True

    def _right_operand(self, index: int) -> str:
        following = self.tokens[index + 1 :]
        if not following or not (following[0].is_word or following[0].text == "("):
            return "..."
        depth = 0
        for token in following:
            depth += (token.text == "(") - (token.text == ")")
            if depth == 0:
                return self.expr[following[0].position : token.position + len(token.text)]
        return self.expr[following[0].position :].rstrip()

    def _mixed(self, index: int, level: _Level) -> SchemaError:
        operator = self.tokens[index]
        start, last = (self.tokens[i].position for i in (level.first or 0, level.last))
        left, tail = (self.expr[i : operator.position].rstrip() for i in (start, last))
        right = self._right_operand(index)
        other = "&" if level.kind == "&" else "+ or -"
        return SchemaError(
            f"operator {operator.text!r} mixes with {other} at one parenthesis level",
            hint=f"write ({left}) {operator.text} {right} or"
            f" {self.expr[start:last]}({tail} {operator.text} {right})",
            context={"position": operator.position, "expression": self.expr},
            code=ErrorCode.MIXED_OPERATORS,
        )


def parse_selector(text: str) -> SelectorAst:
    """Parses selector text into a postfix program, checking its syntax but not its words.

    `+` and `-` share one precedence and combine left to right; `&` chains with itself; mixing
    `&` with `+` or `-` at one parenthesis level raises SchemaError MIXED_OPERATORS at the
    second operator kind, with both parenthesized readings in the hint.

    >>> [token.text for token in parse_selector("(T1 + T2) & T3").program]
    ['T1', 'T2', '+', 'T3', '&']
    """
    return _Parser(_tokenize(text), text).parse()


def _evaluate(ast: SelectorAst, universe: _Universe) -> tuple[SpotId, ...]:
    """Runs a postfix program on ordered sets of universe indices with one explicit stack.

    A result keeps its left operand's order, then adds new IDs from the right in their order.
    """
    names = list(universe.ids)
    index = {spot_id: i for i, spot_id in enumerate(names)}
    stack: list[dict[int, None]] = []
    for token in ast.program:
        if token.is_word:
            found = _resolve_word(universe, token, ast.text)
            stack.append(dict.fromkeys(_interned(index, names, spot_id) for spot_id in found))
            continue
        right = stack.pop()
        left = stack.pop()
        if token.text == "+":
            stack.append(left | right)
        else:
            keeps = token.text == "&"
            stack.append({i: None for i in left if (i in right) == keeps})
    return tuple(names[i] for i in stack[0])


def _interned(index: dict[SpotId, int], names: list[SpotId], spot_id: SpotId) -> int:
    if spot_id not in index:
        index[spot_id] = len(names)
        names.append(spot_id)
    return index[spot_id]


def _validated(universe: _Universe, ids: Sequence[str]) -> Selection:
    known: dict[str, SpotId] = {spot_id: spot_id for spot_id in universe.ids}
    for spot_id in ids:
        if spot_id not in known:
            close = close_matches(spot_id, universe.ids)
            raise UnknownRefError(
                f"unknown spot {spot_id!r} in the {universe.source}",
                hint=f"did you mean {', '.join(close)}?" if close else "check the spot ids",
                context={"spot": spot_id},
            )
    return Selection(ids=tuple(known[spot_id] for spot_id in ids))


def _id_sequence(expr: object) -> tuple[str, ...]:
    if not isinstance(expr, Iterable):
        raise SchemaError(
            f"cannot select with {type(expr).__name__}",
            hint="pass an expression string, a list of spot IDs or a Selection",
            context={"type": type(expr).__name__},
        )
    ids = tuple(expr)
    for spot_id in ids:
        if not isinstance(spot_id, str):
            raise SchemaError(
                f"spot IDs must be strings, got {type(spot_id).__name__}",
                hint="pass a list of ID strings such as ['T1', 'T2']",
                context={"value": repr(spot_id)},
            )
    return tuple(natural_sort(ids)) if isinstance(expr, set | frozenset) else ids


def select(source: "Show | Set | Cast", expr: Selector) -> Selection:
    """Resolves a selector against a show, set or cast.

    `+` and `-` apply left to right; `&` chains with itself but needs parentheses to mix with
    `+` or `-` (SchemaError MIXED_OPERATORS otherwise). Ranges
    keep range order and skip missing middle labels; other words keep creation order (cast
    order, or placement order for a Set); `form:` keeps member order. `T*` matches labels
    whose prefix before the trailing number is exactly `T`. ID lists and Selections pass
    through after every ID is checked; a set is natural-sorted first.

    Args:
        source: Show, Set or Cast to resolve against; a Set cannot resolve group, tag,
            shape or sel words, a Cast cannot resolve form words.
        expr: Expression text, a list of IDs or a Selection.

    Returns:
        selection: The picked spots.

    >>> from showpy.examples import two_set_show
    >>> select(two_set_show(), "T1-T4 - (T2 + T3)").ids
    ('T1', 'T4')
    """
    universe = _universe(source)
    if isinstance(expr, Selection):
        return _validated(universe, expr.ids).replace(description=expr.description)
    if not isinstance(expr, str):
        return _validated(universe, _id_sequence(expr))
    return Selection(ids=_evaluate(parse_selector(expr), universe), description=expr)


def is_label_dependent(expr: str) -> bool:
    """Reports whether an expression names spots by label (IDs, ranges or prefixes).

    Such expressions change meaning when spots are relabeled; group, form, tag, shape, sel
    and `all` words do not.

    >>> is_label_dependent("group:trumpets - T3")
    True
    """
    words = (t.text for t in _tokenize(expr) if t.is_word)
    return any(word != ALL_WORD and KIND_SEPARATOR not in word for word in words)


def _as_area(shape: RegionLike) -> Area:
    if isinstance(shape, shapely.Polygon | shapely.MultiPolygon):
        return shape
    if isinstance(shape, Region):
        return shape.to_shapely()
    return region(shape)


def _on_field(set_: Set) -> list[Placement]:
    return [p for p in set_.placements if p.on_field]


def in_region(set_: Set, shape: RegionLike) -> Selection:
    """Picks on-field spots inside a ring of points or a polygon; the boundary counts as inside.

    >>> s = Set(id="1", counts=0, placements=(Placement(entity="A", x=0, y=0),))
    >>> in_region(s, [(0, 0), (1, 0), (1, 1)]).ids
    ('A',)
    """
    area = _as_area(shape)
    placed = _on_field(set_)
    if not placed:
        return Selection(ids=(), description="region")
    inside = shapely.covers(area, shapely.points([p.point for p in placed]))
    return Selection(
        ids=tuple(p.entity for p, hit in zip(placed, inside, strict=True) if hit),
        description="region",
    )


def near(set_: Set, point: Point, radius: Meters) -> Selection:
    """Picks on-field spots at most `radius` meters from a point, in placement order.

    >>> s = Set(id="1", counts=0, placements=(Placement(entity="A", x=3, y=4),))
    >>> near(s, (0, 0), 5.0).ids
    ('A',)
    """
    if radius < 0:
        raise GeometryError(
            "radius must not be negative", hint="pass radius >= 0", context={"radius": radius}
        )
    found = tuple(p.entity for p in _on_field(set_) if math.dist(p.point, point) <= radius)
    return Selection(ids=found, description=f"near {point} r={radius}")


def where(set_: Set, predicate: Callable[[Placement], bool]) -> Selection:
    """Picks spots whose placement passes `predicate`, off-field placements included.

    >>> s = Set(id="1", counts=0, placements=(Placement(entity="A", x=3, y=4),))
    >>> where(s, lambda p: p.x > 1).ids
    ('A',)
    """
    return Selection(ids=tuple(p.entity for p in set_.placements if predicate(p)))


def _form_key(set_: Set) -> Callable[[Placement], tuple[float, ...]]:
    rank = {form: i for i, form in enumerate(set_.forms)}

    def key(p: Placement) -> tuple[float, ...]:
        form_rank = math.inf if p.form is None else rank.get(p.form, len(rank))
        return form_rank, _order_key(p)

    return key


def _position_key(by: OrderBy, set_: Set) -> Callable[[Placement], tuple[float, ...]]:
    if by == "x":
        return lambda p: (p.x,)
    if by == "y":
        return lambda p: (p.y,)
    if by == "form":
        return _form_key(set_)
    if isinstance(by, Curve):
        curve = by
        return lambda p: (curve.project(p.point),)
    raise SchemaError(
        f"cannot order by {by!r}", hint='use "form", "x", "y" or a Curve', context={"by": by}
    )


def ordered_along(set_: Set, sel: Selector, by: OrderBy = "form") -> Selection:
    """Orders spots by form order, x, y or projected arc length along a curve; ties by ID.

    `form` ranks forms by their order in the set, then by Placement.order; spots without
    a form or order come last. Off-field spots keep their stored position.

    >>> s = Set(id="1", counts=0, placements=(
    ...     Placement(entity="A", x=2, y=0), Placement(entity="B", x=1, y=0)))
    >>> ordered_along(s, "all", by="x").ids
    ('B', 'A')
    """
    chosen = select(set_, sel)
    key = _position_key(by, set_)
    id_rank = {spot_id: i for i, spot_id in enumerate(natural_sort(chosen.ids))}
    placements = [set_.get(i) for i in chosen.ids]
    ordered = sorted(placements, key=lambda p: (*key(p), id_rank[p.entity]))
    return Selection(ids=tuple(p.entity for p in ordered), description=chosen.description)


def _require_parts(k: int, limit: int = MAX_RANGE) -> None:
    if k < 1:
        raise SchemaError("k must be at least 1", hint="pass k >= 1", context={"k": k})
    require_size(k, min(limit, MAX_RANGE), what="parts")


def split(sel: Selection | Sequence[str], k: int) -> list[Selection]:
    """Cuts a selection into k consecutive groups whose sizes differ by at most one.

    Larger groups come first. k is at most the number of spots (1 for an empty selection)
    and at most 10,000.

    >>> [p.ids for p in split(["A", "B", "C"], 2)]
    [('A', 'B'), ('C',)]
    """
    ids = operand_ids(sel)
    _require_parts(k, max(len(ids), 1))
    size, extra = divmod(len(ids), k)
    parts, start = [], 0
    for index in range(k):
        stop = start + size + (1 if index < extra else 0)
        part = {"ids": ids[start:stop], "description": f"part {index + 1}/{k}"}
        parts.append(Selection.model_validate(part))
        start = stop
    return parts


def alternate(sel: Selection | Sequence[str], k: int = 2) -> list[Selection]:
    """Deals a selection into k interleaved groups: group i holds every k-th spot from i.

    >>> [p.ids for p in alternate(["A", "B", "C"])]
    [('A', 'C'), ('B',)]
    """
    _require_parts(k)
    ids = operand_ids(sel)
    return [
        Selection.model_validate({"ids": ids[i::k], "description": f"every {k} from {i + 1}"})
        for i in range(k)
    ]


def mirror_pairs(
    set_: Set, sel: Selector, axis_x: Meters = 0.0, tol: Meters = 0.1
) -> list[tuple[SpotId, SpotId]]:
    """Pairs on-field spots left of `axis_x` with partners right of it.

    The pairing minimizes the total distance between each left spot's mirror image and its
    partner (Hungarian method); pairs farther apart than `tol` are dropped. Spots on the axis
    are their own mirror and never pair. Pairs come as (left, right) in selection order.

    >>> s = Set(id="1", counts=0, placements=(
    ...     Placement(entity="L", x=-1, y=0), Placement(entity="R", x=1, y=0)))
    >>> mirror_pairs(s, "all")
    [('L', 'R')]
    """
    placed = [p for p in (set_.get(i) for i in select(set_, sel).ids) if p.on_field]
    left = [p for p in placed if p.x < axis_x - AXIS_EPS]
    right = [p for p in placed if p.x > axis_x + AXIS_EPS]
    if not left or not right:
        return []
    mirrored = np.array([(2 * axis_x - p.x, p.y) for p in left])
    targets = np.array([p.point for p in right])
    cost = np.linalg.norm(mirrored[:, None, :] - targets[None, :, :], axis=2)
    rows, cols = linear_sum_assignment(cost)
    return [
        (left[r].entity, right[c].entity)
        for r, c in sorted(zip(rows, cols, strict=True))
        if cost[r, c] <= tol
    ]

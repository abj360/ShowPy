"""
publish.py --- publishing a show as a live link, claims on its spots and performer views

Every function is pure: claims live inside the immutable Publication (ADR 0001 item 13),
edits return a new Publication, and every time stamp is an explicit `at` (item 8).
Notifications are returned, never sent (item 20).

Contains:
    publish(): publishes a show revision, carrying forward claims that still qualify
    verify_join_token(): checks a join token against the publication's stored hash
    join_link(): builds the joinable link from a verified join token
    unpublish(): closes a publication
    pause(): pauses a publication
    qr_code(): renders the joinable link of a verified token as a PNG QR code
    claim_spot(): puts a performer on a spot, one each way, locked on claim
    claims(): lists every claim
    who_is_on(): returns the performer holding a spot
    unclaimed(): lists claimable spots nobody holds yet
    swap_claims(): swaps the performers of two claimed spots, as the designer
    reassign(): moves a performer onto a spot, as the designer
    release(): frees a spot, as the designer
    performer_view(): builds a performer's own view of the show
    notify_changes(): lists performers whose part changed, with a terse summary each
"""

import hashlib
import hmac
import io
import ipaddress
import math
import re
from datetime import datetime
from urllib.parse import SplitResult, quote, urlsplit, urlunsplit

import segno
from PIL import Image

from showpy.cast.models import Spot
from showpy.choreo.models import Placement, Set
from showpy.core.caps import MAX_IMAGE_PIXELS, require_size
from showpy.core.errors import LockedError, SchemaError, UnknownRefError, ValidationError
from showpy.core.ids import SpotId, TransitionId, format_tid
from showpy.core.units import angle_diff
from showpy.core.util import close_matches, content_hash, format_ranges, natural_sort
from showpy.export.models import (
    JOIN_PARAMETER,
    Claim,
    Performer,
    PerformerView,
    Publication,
    PublicationStatus,
)
from showpy.export.print_sheets import OFF_FIELD
from showpy.sheet.coords import to_coords
from showpy.show.models import Show

JOIN_TOKEN_FORMAT = re.compile(r"[A-Za-z0-9_-]{22,256}")
BASE_URL_SCHEME = "https"
PRINTABLE_ASCII = re.compile(r"[\x21-\x7e]*")
PATH_CHARS = re.compile(r"(?:[A-Za-z0-9._~!$&()*+,;=:@/-]|%[0-9A-Fa-f]{2})*")
HOST_LABEL = re.compile(r"[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?")
HOST_MAX_LENGTH = 253
PORT_MAX = 65535
DOT_SEGMENTS = (".", "..")
QR_ERROR_LEVEL = "m"
# The QR standard requires a quiet zone four modules wide around the symbol.
QR_QUIET_MODULES = 4
DARK, LIGHT = 0, 255
POSITION_TOL = 1e-6
FACING_TOL = 1e-6
PATH_SEGMENT = "spots"
PATH_SUFFIX = "path"
PART_SEPARATOR = "; "
SPOT_REMOVED = "spot removed"


def _require_aware(at: datetime) -> None:
    if at.tzinfo is None or at.utcoffset() is None:
        raise SchemaError(
            f"time stamp {at.isoformat()} has no time zone",
            hint="pass an aware datetime, e.g. datetime(2026, 11, 11, tzinfo=UTC)",
            context={"at": at.isoformat()},
        )


def _sorted(claims_: list[Claim]) -> tuple[Claim, ...]:
    order = {spot: i for i, spot in enumerate(natural_sort(c.spot for c in claims_))}
    return tuple(sorted(claims_, key=lambda c: order[c.spot]))


def _may_claim(spot: Spot, performer: Performer) -> bool:
    return spot.active and spot.claimable == performer.role


def _still_valid(show: Show, claim: Claim) -> bool:
    return claim.spot in show.cast.ids() and _may_claim(show.cast.spot(claim.spot), claim.performer)


def _require_join_token(join_token: object) -> None:
    if not isinstance(join_token, str) or JOIN_TOKEN_FORMAT.fullmatch(join_token) is None:
        raise SchemaError(
            "join token must be 22 to 256 characters of A-Z, a-z, 0-9, _ or -",
            hint="generate one with `secrets.token_urlsafe(16)`",
            context={
                "type": type(join_token).__name__,
                "length": len(join_token) if isinstance(join_token, str) else None,
            },
        )


def _token_digest(join_token: str) -> str:
    return hashlib.sha256(join_token.encode()).hexdigest()


def _issued_digest(
    publication_id: str, previous: Publication | None, join_token: str | None
) -> str:
    if join_token is not None:
        return _token_digest(join_token)
    if previous is not None and previous.join_token_sha256 is not None:
        return previous.join_token_sha256
    raise SchemaError(
        f"publication {publication_id} needs a join token: it has no stored digest to keep",
        hint="pass join_token=secrets.token_urlsafe(16); a republish may omit it to keep the token",
        context={"publication_id": publication_id, "has_previous": previous is not None},
    )


def _reject_base_url(reason: str, parts_scheme: str = "", host: str | None = None) -> SchemaError:
    return SchemaError(
        f"base URL {reason}",
        hint="pass base_url like https://eight.example/p",
        context={"scheme": parts_scheme, "host": host},
    )


def _is_valid_host(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return True
    labels = host.split(".")
    return len(host) <= HOST_MAX_LENGTH and all(HOST_LABEL.fullmatch(label) for label in labels)


def _split_base_url(base_url: str) -> SplitResult:
    if PRINTABLE_ASCII.fullmatch(base_url) is None or "\\" in base_url:
        raise _reject_base_url("has whitespace, control, non-ASCII or backslash characters")
    try:
        parts = urlsplit(base_url)
        parts.port  # noqa: B018 - raises ValueError on a bad port
    except ValueError as error:
        raise _reject_base_url("does not parse") from error
    return parts


def _require_https_base_url(base_url: str) -> str:
    parts = _split_base_url(base_url)
    host = parts.hostname
    scheme = parts.scheme
    if scheme != BASE_URL_SCHEME or not host:
        raise _reject_base_url("must be https with a host", scheme, host)
    if "@" in parts.netloc or "%" in parts.netloc or parts.netloc.endswith(":"):
        raise _reject_base_url(
            "has credentials, escapes or an empty port in its host", scheme, host
        )
    if parts.query or parts.fragment or "?" in base_url or "#" in base_url:
        raise _reject_base_url("has a query or fragment", scheme, host)
    is_bracketed = parts.netloc.startswith("[")
    if not is_bracketed and not _is_valid_host(host):
        raise _reject_base_url("has an invalid host", scheme, host)
    if is_bracketed and ":" not in host:
        raise _reject_base_url("has an invalid IPv6 host", scheme, host)
    if parts.port is not None and not 0 < parts.port <= PORT_MAX:
        raise _reject_base_url("has a port outside 1-65535", scheme, host)
    if PATH_CHARS.fullmatch(parts.path) is None:
        raise _reject_base_url("has characters outside RFC 3986 path characters", scheme, host)
    if any(segment in DOT_SEGMENTS for segment in parts.path.split("/")):
        raise _reject_base_url("has a dot segment in its path", scheme, host)
    return urlunsplit(parts).rstrip("/")


def publish(
    show: Show,
    publication_id: str,
    base_url: str,
    at: datetime,
    previous: Publication | None = None,
    *,
    join_token: str | None = None,
) -> Publication:
    """Publishes the show's current revision as a live link that only a join token opens.

    Args:
        show: Show to publish.
        publication_id: Stable ID; republishing reuses it.
        base_url: Public https URL prefix the link is built on; no credentials, query or fragment.
        at: Publication time; must carry a time zone.
        previous: The publication being updated; its claims carry over when the spot still
            exists and is still claimable by that performer's role.
        join_token: Secret of 22 to 256 characters of `[A-Za-z0-9_-]`, required on the first
            publish. The publication stores only its SHA-256 digest; `join_link` builds the
            joinable link. On a republish, None keeps the previous digest and a token rotates it.

    Returns:
        publication: A live publication of the show's revision (its content hash when unsaved),
            holding no join token.

    >>> from datetime import UTC, datetime
    >>> from showpy.examples import two_set_show
    >>> token = "k3Qz9-high-entropy-join-token"
    >>> at = datetime(2026, 1, 1, tzinfo=UTC)
    >>> publish(two_set_show(), "p1", "https://x.example", at, join_token=token).link
    'https://x.example/p1'
    """
    _require_aware(at)
    if join_token is not None:
        _require_join_token(join_token)
    if not publication_id.strip():
        raise SchemaError(
            "publication needs an ID",
            hint="pass a non-blank publication_id",
            context={"publication_id": publication_id},
        )
    root = _require_https_base_url(base_url)
    if previous is not None and (previous.id, previous.show_id) != (publication_id, show.id):
        raise ValidationError(
            f"publication {previous.id} of {previous.show_id} cannot be republished as "
            f"{publication_id} of {show.id}",
            hint="republish with the same publication ID and show",
            context={"previous": previous.id, "publication_id": publication_id},
        )
    digest = _issued_digest(publication_id, previous, join_token)
    kept = [c for c in previous.claims if _still_valid(show, c)] if previous else []
    return Publication(
        id=publication_id,
        show_id=show.id,
        revision=show.revision or content_hash(show),
        link=f"{root}/{quote(publication_id, safe='')}",
        join_token_sha256=digest,
        published_at=at,
        claims=_sorted(kept),
    )


def verify_join_token(pub: Publication, token: str) -> bool:
    """Checks a join token against the publication's stored hash; False when none is stored.

    Ignores the publication's status: `claim_spot` is what gates on `live`. Tokens should come
    from `secrets.token_urlsafe(16)`.

    Args:
        pub: Publication holding the stored SHA-256 digest.
        token: Token presented by a performer.

    Returns:
        is_match: Whether the token is well formed and hashes to the stored digest.

    >>> from showpy.examples import two_set_show
    >>> from datetime import UTC, datetime
    >>> token = "k3Qz9-high-entropy-join-token"
    >>> at = datetime(2026, 1, 1, tzinfo=UTC)
    >>> pub = publish(two_set_show(), "p1", "https://x.example", at, join_token=token)
    >>> verify_join_token(pub, token), verify_join_token(pub, "wrong")
    (True, False)
    """
    if not isinstance(token, str) or pub.join_token_sha256 is None:
        return False
    if JOIN_TOKEN_FORMAT.fullmatch(token) is None:
        return False
    return hmac.compare_digest(_token_digest(token), pub.join_token_sha256)


def join_link(pub: Publication, token: str) -> str:
    """Builds the joinable link `<pub.link>?t=<token>` once the token matches the stored digest.

    The publication never holds the token, so the host passes the one it issued at publish.
    A stored link that already has a query or fragment is refused; republishing rebuilds it.

    Args:
        pub: Publication whose link performers join through.
        token: Join token issued with `publish(..., join_token=)`.

    Returns:
        link: The link performers open, carrying the token.

    >>> from showpy.examples import two_set_show
    >>> from datetime import UTC, datetime
    >>> token = "k3Qz9-high-entropy-join-token"
    >>> at = datetime(2026, 1, 1, tzinfo=UTC)
    >>> pub = publish(two_set_show(), "p1", "https://x.example", at, join_token=token)
    >>> join_link(pub, token)
    'https://x.example/p1?t=k3Qz9-high-entropy-join-token'
    """
    _require_join_token(token)
    if "?" in pub.link or "#" in pub.link:
        raise ValidationError(
            f"publication {pub.id} link has a query or fragment, so no token can follow it",
            hint="republish it with publish(..., previous=pub) to rebuild the link",
            context={"publication": pub.id},
        )
    if pub.join_token_sha256 is None:
        raise ValidationError(
            f"publication {pub.id} stores no join token digest, so nobody can join it",
            hint="republish it with join_token=secrets.token_urlsafe(16) to issue one",
            context={"publication": pub.id},
        )
    if not verify_join_token(pub, token):
        raise ValidationError(
            f"join token does not match publication {pub.id}",
            hint="pass the token issued at publish; republish with a new join_token if it is lost",
            context={"publication": pub.id},
        )
    return f"{pub.link}?{JOIN_PARAMETER}={token}"


def _with_status(pub: Publication, status: PublicationStatus) -> Publication:
    return pub.replace(status=status)


def unpublish(pub: Publication) -> Publication:
    """Closes a publication; claims stay on record.

    >>> from showpy.examples import live_publication
    >>> from showpy.examples import two_set_show
    >>> unpublish(live_publication(two_set_show())).status
    'closed'
    """
    return _with_status(pub, "closed")


def pause(pub: Publication) -> Publication:
    """Pauses a publication: the link stays but nobody can claim.

    >>> from showpy.examples import live_publication
    >>> from showpy.examples import two_set_show
    >>> pause(live_publication(two_set_show())).status
    'paused'
    """
    return _with_status(pub, "paused")


def _require_pixel_size(size: int) -> None:
    if isinstance(size, bool) or not isinstance(size, int) or size < 1:
        raise SchemaError(
            f"QR code size must be a whole number of pixels, at least 1, got {size!r}",
            hint="pass e.g. size=512",
            context={"size": repr(size)},
        )
    require_size(size * size, MAX_IMAGE_PIXELS, what="QR code pixels")


def _make_code(link: str) -> segno.QRCode:
    try:
        return segno.make(link, error=QR_ERROR_LEVEL, micro=False, boost_error=False)
    except segno.DataOverflowError as err:
        raise SchemaError(
            "the publication link is too long for a QR code",
            hint="shorten the base URL or the publication ID",
            context={"link_length": len(link)},
        ) from err


def qr_code(pub: Publication, token: str, size: int = 512) -> bytes:
    r"""Renders `join_link(pub, token)` as a square PNG QR code of `size` pixels.

    Modules are whole pixels, centered with the standard quiet zone, so the code stays sharp.

    Args:
        pub: Publication whose joinable link is encoded.
        token: Join token issued at publish; a token that does not verify raises.
        size: Width and height of the image in pixels.

    Returns:
        png: The PNG bytes of the code.

    >>> from showpy.examples import two_set_show
    >>> from datetime import UTC, datetime
    >>> token = "k3Qz9-high-entropy-join-token"
    >>> at = datetime(2026, 1, 1, tzinfo=UTC)
    >>> pub = publish(two_set_show(), "p1", "https://x.example", at, join_token=token)
    >>> qr_code(pub, token, size=128)[:4]
    b'\x89PNG'
    """
    link = join_link(pub, token)
    _require_pixel_size(size)
    code = _make_code(link)
    rows = [
        tuple(bool(m) for m in row) for row in code.matrix_iter(scale=1, border=QR_QUIET_MODULES)
    ]
    modules = len(rows)
    if size < modules:
        raise SchemaError(
            f"QR code needs at least {modules} pixels, got {size}",
            hint=f"pass size >= {modules}",
            context={"size": size, "modules": modules},
        )
    symbol = Image.new("L", (modules, modules), LIGHT)
    symbol.putdata([DARK if dark else LIGHT for row in rows for dark in row])
    scaled = size // modules * modules
    canvas = Image.new("L", (size, size), LIGHT)
    offset = (size - scaled) // 2
    canvas.paste(symbol.resize((scaled, scaled), Image.Resampling.NEAREST), (offset, offset))
    out = io.BytesIO()
    canvas.save(out, format="PNG")
    return out.getvalue()


def claims(pub: Publication) -> list[Claim]:
    """Lists every claim in natural spot order.

    >>> from showpy.examples import live_publication
    >>> from showpy.examples import two_set_show
    >>> claims(live_publication(two_set_show()))
    []
    """
    return list(pub.claims)


def _claim_on(pub: Publication, spot: SpotId) -> Claim | None:
    return next((c for c in pub.claims if c.spot == spot), None)


def who_is_on(pub: Publication, spot: SpotId) -> Performer | None:
    """Returns the performer holding a spot, or None when it is free.

    >>> from showpy.examples import live_publication
    >>> from showpy.examples import two_set_show
    >>> who_is_on(live_publication(two_set_show()), "T1") is None
    True
    """
    claim = _claim_on(pub, spot)
    return None if claim is None else claim.performer


def unclaimed(pub: Publication, show: Show) -> list[SpotId]:
    """Lists active, claimable spots nobody holds yet, in cast order.

    >>> from showpy.examples import live_publication
    >>> from showpy.examples import two_set_show
    >>> unclaimed(live_publication(two_set_show()), two_set_show())
    ['T1', 'T2', 'T3', 'T4']
    """
    held = {c.spot for c in pub.claims}
    return [
        s.id for s in show.cast.spots if s.active and s.claimable != "none" and s.id not in held
    ]


def _require_claimable(pub: Publication, show: Show, performer: Performer, spot: Spot) -> None:
    holder = who_is_on(pub, spot.id)
    if holder is not None:
        free = format_ranges(unclaimed(pub, show)) or "none"
        raise LockedError(
            f"{spot.id} is already claimed",
            hint=f"pick a free spot: {free}",
            context={"spot": spot.id, "holder": holder.id},
        )
    mine = next((c.spot for c in pub.claims if c.performer.id == performer.id), None)
    if mine is not None:
        raise LockedError(
            f"{performer.name} already holds {mine}",
            hint="one spot per performer; ask the designer to reassign you",
            context={"performer": performer.id, "spot": mine},
        )
    if not _may_claim(spot, performer):
        raise ValidationError(
            f"{spot.id} cannot be claimed by a {performer.role}",
            hint=f"{performer.role}s may claim active spots marked {performer.role}",
            context={"spot": spot.id, "claimable": spot.claimable, "active": spot.active},
        )


def claim_spot(
    pub: Publication, show: Show, performer: Performer, spot: SpotId, at: datetime
) -> tuple[Publication, Claim]:
    """Puts a performer on a spot: one spot per performer, one performer per spot.

    Args:
        pub: Live publication of `show`.
        show: The published show, for spot roles.
        performer: Who claims; only spots whose `claimable` matches their role qualify.
        spot: Spot to claim.
        at: Claim time.

    Returns:
        result: The new publication and the locked claim.

    >>> from showpy.examples import EXAMPLE_TIME as AT, live_publication, performer
    >>> from showpy.examples import two_set_show
    >>> show = two_set_show()
    >>> claim_spot(live_publication(show), show, performer("Ada"), "T1", AT)[1].locked
    True
    """
    if pub.show_id != show.id:
        raise ValidationError(
            f"publication {pub.id} is of show {pub.show_id}, not {show.id}",
            hint="pass the show the publication was made from",
            context={"publication": pub.id, "show": show.id},
        )
    if pub.status != "live":
        raise LockedError(
            f"publication {pub.id} is {pub.status}",
            hint="claims open only while the publication is live",
            context={"status": pub.status},
        )
    _require_claimable(pub, show, performer, show.cast.spot(spot))
    claim = Claim(spot=spot, performer=performer, claimed_at=at)
    return pub.replace(claims=_sorted([*pub.claims, claim])), claim


def _require_open(pub: Publication) -> None:
    if pub.status == "closed":
        raise LockedError(
            f"publication {pub.id} is closed",
            hint="republish the show before changing claims",
            context={"publication": pub.id},
        )


def _require_claimed(pub: Publication, spot: SpotId) -> Claim:
    claim = _claim_on(pub, spot)
    if claim is None:
        held = [c.spot for c in pub.claims]
        close = close_matches(spot, held)
        raise UnknownRefError(
            f"nobody holds {spot}",
            hint=f"did you mean {', '.join(close)}?" if close else f"claimed: {held or 'none'}",
            context={"spot": spot},
        )
    return claim


def swap_claims(pub: Publication, a: SpotId, b: SpotId) -> Publication:
    """Swaps the performers of two claimed spots, as the designer; roles must match.

    >>> from showpy.examples import EXAMPLE_TIME as AT, live_publication, performer
    >>> from showpy.examples import two_set_show
    >>> show = two_set_show()
    >>> pub, _ = claim_spot(live_publication(show), show, performer("Ada"), "T1", AT)
    >>> pub, _ = claim_spot(pub, show, performer("Bo"), "T2", AT)
    >>> who_is_on(swap_claims(pub, "T1", "T2"), "T1").name
    'Bo'
    """
    _require_open(pub)
    first, second = _claim_on(pub, a), _claim_on(pub, b)
    if first is None or second is None:
        raise ValidationError(
            f"swap needs both {a} and {b} claimed",
            hint="use reassign() to move a performer onto an empty spot",
            context={"a": a, "b": b},
        )
    if first.performer.role != second.performer.role:
        raise ValidationError(
            f"{a} and {b} are held by a {first.performer.role} and a {second.performer.role}",
            hint="swap performers of the same role",
            context={"a": a, "b": b},
        )
    others = [c for c in pub.claims if c.spot not in (a, b)]
    swapped = [first.replace(spot=b), second.replace(spot=a)]
    return pub.replace(claims=_sorted(others + swapped))


def reassign(pub: Publication, spot: SpotId, performer: Performer, at: datetime) -> Publication:
    """Moves a performer onto a spot, as the designer; whoever held the spot is unassigned.

    >>> from showpy.examples import EXAMPLE_TIME as AT, live_publication, performer
    >>> from showpy.examples import two_set_show
    >>> pub = reassign(live_publication(two_set_show()), "T3", performer("Ada"), AT)
    >>> who_is_on(pub, "T3").name
    'Ada'
    """
    _require_open(pub)
    kept = [c for c in pub.claims if c.spot != spot and c.performer.id != performer.id]
    claim = Claim(spot=spot, performer=performer, claimed_at=at)
    return pub.replace(claims=_sorted([*kept, claim]))


def release(pub: Publication, spot: SpotId) -> Publication:
    """Frees a claimed spot, as the designer.

    >>> from showpy.examples import EXAMPLE_TIME as AT, live_publication, performer
    >>> from showpy.examples import two_set_show
    >>> pub = reassign(live_publication(two_set_show()), "T3", performer("Ada"), AT)
    >>> who_is_on(release(pub, "T3"), "T3") is None
    True
    """
    _require_open(pub)
    _require_claimed(pub, spot)
    return pub.replace(claims=tuple(c for c in pub.claims if c.spot != spot))


def performer_view(pub: Publication, show: Show, performer: Performer) -> PerformerView:
    """Builds a performer's own view: their spot's position text and counts in every set.

    >>> from showpy.examples import EXAMPLE_TIME as AT, live_publication, performer
    >>> from showpy.examples import two_set_show
    >>> show = two_set_show()
    >>> pub, _ = claim_spot(live_publication(show), show, performer("Ada"), "T1", AT)
    >>> performer_view(pub, show, performer("Ada")).sets[1][2]
    8.0
    """
    claim = next((c for c in pub.claims if c.performer.id == performer.id), None)
    if claim is None:
        raise UnknownRefError(
            f"{performer.name} holds no spot in publication {pub.id}",
            hint=f"claim one first; free spots: {format_ranges(unclaimed(pub, show)) or 'none'}",
            context={"performer": performer.id},
        )
    rows = []
    for current in show.sets:
        placement = current.get(claim.spot)
        text = to_coords(show, placement.point) if placement.on_field else OFF_FIELD
        rows.append((current.id, text, current.counts))
    link = urlsplit(pub.link)
    root = urlunsplit(link._replace(query="", fragment="")).rstrip("/")
    url = f"{root}/{PATH_SEGMENT}/{quote(claim.spot, safe='')}/{PATH_SUFFIX}"
    return PerformerView(
        performer=claim.performer,
        spot=claim.spot,
        sets=tuple(rows),
        path_animation_url=url,
    )


def _placement(current: Set, spot: SpotId) -> Placement | None:
    return next((p for p in current.placements if p.entity == spot), None)


def _set_changes(old: Show, new: Show, spot: SpotId) -> tuple[list[str], list[str]]:
    moved, turned = [], []
    old_sets = {s.id: s for s in old.sets}
    for current in new.sets:
        before = _placement(old_sets[current.id], spot) if current.id in old_sets else None
        after = _placement(current, spot)
        if before is None or after is None:
            continue
        if math.dist(before.point, after.point) > POSITION_TOL or before.on_field != after.on_field:
            moved.append(current.id)
        if abs(angle_diff(before.facing, after.facing)) > FACING_TOL:
            turned.append(current.id)
    return moved, turned


def _path_key(show: Show, tid: TransitionId, spot: SpotId) -> tuple[object, ...]:
    spec = show.transition(tid)
    row = spec.row(spot)
    return row, spec.routes.get(row.route) if row.route else None, spec.counts


def _path_changes(old: Show, new: Show, spot: SpotId) -> list[str]:
    shared = [tid for tid in new.transition_ids() if tid in old.transition_ids()]
    return [
        format_tid(tid) for tid in shared if _path_key(old, tid, spot) != _path_key(new, tid, spot)
    ]


def _phrase(what: str, places: list[str], noun: str | None = None) -> str | None:
    if not places:
        return None
    label = "" if noun is None else f"{noun}{'' if len(places) == 1 else 's'} "
    return f"{what} in {label}{', '.join(places)}"


def _summary(old: Show, new: Show, spot: SpotId) -> str:
    if spot not in new.cast.ids():
        return SPOT_REMOVED
    moved, turned = _set_changes(old, new, spot)
    parts = [
        _phrase("moved", moved, "set"),
        _phrase("facing changed", turned, "set"),
        _phrase("path changed", _path_changes(old, new, spot)),
    ]
    return PART_SEPARATOR.join(p for p in parts if p)


def notify_changes(pub: Publication, old_show: Show, new_show: Show) -> list[tuple[Performer, str]]:
    """Lists performers whose spot moved, turned, lost its path or was removed, with a summary.

    Nothing is sent; callers deliver the messages.

    >>> from showpy.examples import EXAMPLE_TIME as AT, live_publication, performer
    >>> from showpy.examples import with_targets
    >>> from showpy.examples import two_set_show
    >>> show = two_set_show()
    >>> pub, _ = claim_spot(live_publication(show), show, performer("Ada"), "T1", AT)
    >>> notify_changes(pub, show, with_targets({"T1": {"x": 3.0}}))[0][1]
    'moved in set 2'
    """
    notices = []
    for claim in pub.claims:
        summary = _summary(old_show, new_show, claim.spot)
        if summary:
            notices.append((claim.performer, summary))
    return notices

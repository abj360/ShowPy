#!/usr/bin/env python3

"""
test_publish.py --- tests publishing, QR codes, claims, designer actions, views and notices

Also holds the audited publishing defects (UP-9.5-01 to UP-9.5-26) as strict xfails.
"""

import io
import resource
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest
import segno
from PIL import Image

from showpy.cast.models import Spot
from showpy.cast.rewrite import rewrite_spot_ids
from showpy.choreo.models import PathRow
from showpy.core.errors import (
    LockedError,
    SchemaError,
    ShowPyError,
    UnknownRefError,
    ValidationError,
)
from showpy.export.models import Performer, Publication
from showpy.export.publish import (
    claim_spot,
    claims,
    notify_changes,
    pause,
    performer_view,
    publish,
    qr_code,
    reassign,
    release,
    swap_claims,
    unclaimed,
    unpublish,
    who_is_on,
)
from showpy.sheet.coords import set_coordinates
from showpy.sheet.models import CoordinateSystem
from showpy.show.models import Show
from tests.fixtures.export_shows import AT, guided_show, performer
from tests.fixtures.motion_shows import with_rows, with_targets
from tests.fixtures.shows import two_set_show

BASE_URL = "https://eight.example/p/"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
LATER = AT + timedelta(minutes=5)
EARLIER = AT - timedelta(days=1)
QR_QUIET_MODULES = 4
HUGE_QR_SIZE = 100_000
QR_MEMORY_CAP_BYTES = 1_500_000_000
OVERLONG_PATH = "a" * 8000
CONTACT = "ada@example.com"


def _published(show: Show | None = None) -> Publication:
    return publish(show or two_set_show(), "pub-1", BASE_URL, AT)


def _with_claims(show: Show, *pairs: tuple[str, str]) -> Publication:
    pub = publish(show, "pub-1", BASE_URL, AT)
    for name, spot in pairs:
        pub, _ = claim_spot(pub, show, performer(name), spot, AT)
    return pub


def _crew_show() -> Show:
    show = two_set_show()
    spots = (
        show.cast.spots[0],
        show.cast.spots[1].model_copy(update={"claimable": "crew"}),
        show.cast.spots[2].model_copy(update={"claimable": "none"}),
        show.cast.spots[3].model_copy(update={"active": False}),
    )
    return show.model_copy(update={"cast": show.cast.model_copy(update={"spots": spots})})


def test_publish_builds_a_live_link() -> None:
    pub = _published()
    assert pub.link == "https://eight.example/p/pub-1"
    assert (pub.status, pub.show_id, pub.published_at, pub.claims) == ("live", "show-1", AT, ())
    assert pub.revision


def test_publish_uses_the_stored_revision_and_is_deterministic() -> None:
    show = two_set_show().model_copy(update={"revision": "rev-9"})
    assert publish(show, "p", BASE_URL, AT).revision == "rev-9"
    assert _published() == _published()


def test_publish_refuses_a_naive_time_or_blank_url() -> None:
    with pytest.raises(SchemaError):
        publish(two_set_show(), "p", BASE_URL, datetime(2026, 1, 1))
    with pytest.raises(SchemaError):
        publish(two_set_show(), "p", " ", AT)


def test_pause_and_unpublish_return_new_publications() -> None:
    pub = _published()
    assert pause(pub).status == "paused"
    assert unpublish(pub).status == "closed"
    assert pub.status == "live"


def test_qr_code_is_a_png_of_the_requested_size() -> None:
    png = qr_code(_published(), size=300)
    assert png.startswith(PNG_SIGNATURE)
    with Image.open(io.BytesIO(png)) as image:
        assert image.size == (300, 300)
        assert image.convert("L").getextrema() == (0, 255)
    assert qr_code(_published()) == qr_code(_published())
    other = publish(two_set_show(), "pub-2", BASE_URL, AT)
    assert qr_code(other) != qr_code(_published())


def test_qr_code_refuses_a_size_too_small_for_the_code() -> None:
    with pytest.raises(SchemaError):
        qr_code(_published(), size=10)


def test_claim_spot_locks_the_spot_for_the_performer() -> None:
    show = two_set_show()
    pub, claim = claim_spot(_published(show), show, performer("Ada"), "T2", LATER)
    assert (claim.spot, claim.performer.name, claim.claimed_at, claim.locked) == (
        "T2",
        "Ada",
        LATER,
        True,
    )
    assert claims(pub) == [claim]
    assert who_is_on(pub, "T2") == performer("Ada")
    assert who_is_on(pub, "T1") is None
    assert unclaimed(pub, show) == ["T1", "T3", "T4"]


def test_one_spot_per_performer_and_one_performer_per_spot() -> None:
    show = two_set_show()
    pub = _with_claims(show, ("Ada", "T1"))
    with pytest.raises(LockedError):
        claim_spot(pub, show, performer("Ada"), "T2", AT)
    with pytest.raises(LockedError) as caught:
        claim_spot(pub, show, performer("Bo"), "T1", AT)
    assert "T2" in caught.value.hint


def test_claims_respect_roles_and_activity() -> None:
    show = _crew_show()
    pub = _published(show)
    with pytest.raises(ValidationError):
        claim_spot(pub, show, performer("Ada"), "T2", AT)
    claim_spot(pub, show, performer("Cy", role="crew"), "T2", AT)
    with pytest.raises(ValidationError):
        claim_spot(pub, show, performer("Ada"), "T3", AT)
    with pytest.raises(ValidationError):
        claim_spot(pub, show, performer("Ada"), "T4", AT)
    assert unclaimed(pub, show) == ["T1", "T2"]


def test_claims_need_a_live_publication_of_the_same_show() -> None:
    show = two_set_show()
    with pytest.raises(LockedError):
        claim_spot(pause(_published(show)), show, performer("Ada"), "T1", AT)
    with pytest.raises(UnknownRefError):
        claim_spot(_published(show), show, performer("Ada"), "T9", AT)
    other = show.model_copy(update={"id": "show-2"})
    with pytest.raises(ValidationError):
        claim_spot(_published(show), other, performer("Ada"), "T1", AT)


def test_republish_keeps_claims_on_spots_that_still_qualify() -> None:
    show = two_set_show()
    pub = _with_claims(show, ("Ada", "T1"), ("Bo", "T2"), ("Cy", "T3"))
    spots = (
        show.cast.spots[0],
        show.cast.spots[1].model_copy(update={"claimable": "crew"}),
        show.cast.spots[3],
    )
    edited = show.model_copy(
        update={
            "cast": show.cast.model_copy(update={"spots": spots}),
            "sets": tuple(
                s.model_copy(
                    update={"placements": tuple(p for p in s.placements if p.entity != "T3")}
                )
                for s in show.sets
            ),
        }
    )
    again = publish(edited, "pub-1", BASE_URL, LATER, previous=pub)
    assert [c.spot for c in again.claims] == ["T1"]
    assert again.published_at == LATER
    with pytest.raises(ValidationError):
        publish(edited, "pub-2", BASE_URL, LATER, previous=pub)


def test_swap_reassign_and_release_are_designer_edits() -> None:
    show = two_set_show()
    pub = _with_claims(show, ("Ada", "T1"), ("Bo", "T2"))
    swapped = swap_claims(pub, "T1", "T2")
    assert (who_is_on(swapped, "T1"), who_is_on(swapped, "T2")) == (
        performer("Bo"),
        performer("Ada"),
    )
    moved = reassign(pub, "T4", performer("Ada"), LATER)
    assert who_is_on(moved, "T1") is None
    assert who_is_on(moved, "T4") == performer("Ada")
    taken = reassign(pub, "T2", performer("Ada"), LATER)
    assert [(c.spot, c.performer.name) for c in taken.claims] == [("T2", "Ada")]
    freed = release(pub, "T1")
    assert who_is_on(freed, "T1") is None
    assert len(pub.claims) == 2


def test_designer_edits_refuse_bad_targets() -> None:
    show = two_set_show()
    pub = _with_claims(show, ("Ada", "T1"), ("Cy", "T3"))
    with pytest.raises(ValidationError):
        swap_claims(pub, "T1", "T2")
    with pytest.raises(UnknownRefError):
        release(pub, "T2")
    with pytest.raises(LockedError):
        release(unpublish(pub), "T1")
    crew = _with_claims(_crew_show(), ("Ada", "T1"))
    crew, _ = claim_spot(crew, _crew_show(), performer("Cy", role="crew"), "T2", AT)
    with pytest.raises(ValidationError):
        swap_claims(crew, "T1", "T2")


def test_performer_view_lists_every_set() -> None:
    show = guided_show()
    pub = _with_claims(show, ("Ada", "T2"))
    view = performer_view(pub, show, performer("Ada"))
    assert view.spot == "T2"
    assert view.sets == (
        ("1", "2.00 m right of center, 5.00 m upstage of front edge", 0.0),
        ("2", "2.00 m right of center, 9.00 m upstage of front edge", 8.0),
    )
    assert view.path_animation_url == "https://eight.example/p/pub-1/spots/T2/path"
    with pytest.raises(UnknownRefError):
        performer_view(pub, show, performer("Bo"))


def test_notify_changes_tells_only_affected_performers() -> None:
    old = two_set_show()
    pub = _with_claims(old, ("Ada", "T1"), ("Bo", "T2"), ("Cy", "T3"), ("Di", "T4"))
    new = with_targets({"T1": {"x": 1.0}, "T2": {"facing": 90.0}}, show=old)
    new = with_rows({"T3": PathRow(entity="T3", start=2.0)}, show=new)
    notices = notify_changes(pub, old, new)
    assert [(p.name, text) for p, text in notices] == [
        ("Ada", "moved in set 2"),
        ("Bo", "facing changed in set 2"),
        ("Cy", "path changed in 1→2"),
    ]


def test_notify_changes_reports_removed_spots_and_combines_parts() -> None:
    old = two_set_show()
    pub = _with_claims(old, ("Ada", "T1"), ("Bo", "T2"))
    edited = [s.with_placements({"T1": {"x": 5.0, "facing": 45.0}}) for s in old.sets]
    sets = tuple(
        s.model_copy(update={"placements": tuple(p for p in s.placements if p.entity != "T2")})
        for s in edited
    )
    spots = tuple(s for s in old.cast.spots if s.id != "T2")
    new = old.model_copy(
        update={"sets": sets, "cast": old.cast.model_copy(update={"spots": spots})}
    )
    notices = {p.name: text for p, text in notify_changes(pub, old, new)}
    assert notices == {
        "Ada": "moved in sets 1, 2; facing changed in sets 1, 2",
        "Bo": "spot removed",
    }


def test_unclaimed_skips_spots_nobody_may_claim() -> None:
    show = two_set_show()
    extra = Spot(id="P1", group="trumpets", claimable="none")
    show = show.model_copy(
        update={"cast": show.cast.model_copy(update={"spots": (*show.cast.spots, extra)})}
    )
    assert "P1" not in unclaimed(_published(show), show)


def _reference_grid(link: str, *, boost: bool = True) -> list[list[bool]]:
    code = segno.make(link, error="m", micro=False, boost_error=boost)
    return [[bool(m) for m in row] for row in code.matrix_iter(scale=1, border=0)]


def _module_grid(png: bytes, link: str) -> list[list[bool]]:
    """Samples the PNG at each module centre; the module count comes from the link's symbol."""
    size = len(_reference_grid(link))
    total = size + 2 * QR_QUIET_MODULES
    with Image.open(io.BytesIO(png)) as image:
        gray = image.convert("L")
        cell = gray.width // total
        origin = (gray.width - cell * total) // 2 + cell * QR_QUIET_MODULES
        return [
            [
                gray.getpixel((origin + col * cell + cell // 2, origin + row * cell + cell // 2))
                == 0
                for col in range(size)
            ]
            for row in range(size)
        ]


def _claimed_with_contact() -> tuple[Show, Publication]:
    show = two_set_show()
    ada = performer("Ada").model_copy(update={"contact": CONTACT})
    pub, _ = claim_spot(_published(show), show, ada, "T1", AT)
    return show, pub


@contextmanager
def _address_space_cap(limit: int) -> Iterator[None]:
    soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    resource.setrlimit(resource.RLIMIT_AS, (limit, hard))
    try:
        yield
    finally:
        resource.setrlimit(resource.RLIMIT_AS, (soft, hard))


def _notified(old: Show, new: Show) -> list[tuple[Performer, str]]:
    return notify_changes(_with_claims(old, ("Ada", "T1")), old, new)


def test_qr_code_matches_the_reference_symbol() -> None:
    pub = _published()
    assert _module_grid(qr_code(pub), pub.link) == _reference_grid(pub.link, boost=True)


@pytest.mark.defect("UP-9.5-01")
@pytest.mark.xfail(strict=True, reason="UP-9.5-01")
def test_regression_publish_link_carries_a_caller_supplied_join_token() -> None:
    """Puts a caller-supplied high-entropy token in the link and stores only its hash.

    The link is `base_url/<publication id>`, so anyone who guesses an ID can open the
    publication and claim with a self-declared performer (audit 9.5 security; P0 #10).
    """
    token = "k3Qz9-high-entropy-join-token"
    pub = publish(two_set_show(), "pub-1", BASE_URL, AT, join_token=token)  # type: ignore[call-arg]
    assert token in pub.link
    assert token not in pub.model_dump_json()


@pytest.mark.defect("UP-9.5-02")
@pytest.mark.xfail(strict=True, reason="UP-9.5-02")
def test_regression_publication_snapshot_excludes_contact_details() -> None:
    """Keeps performer contact data out of the serialized publication (audit 9.5; P0 #10).

    `Claim.performer.contact` serializes with the snapshot that is shared with every viewer.
    """
    _, pub = _claimed_with_contact()
    assert CONTACT not in pub.model_dump_json()


@pytest.mark.defect("UP-9.5-03")
@pytest.mark.xfail(strict=True, reason="UP-9.5-03")
def test_regression_claims_and_notices_return_redacted_performers() -> None:
    """Returns performers without contact data from claims() and notify_changes() (audit 9.5)."""
    old, pub = _claimed_with_contact()
    new = with_targets({"T1": {"x": 3.0}}, show=old)
    assert all(c.performer.contact is None for c in claims(pub))
    notices = notify_changes(pub, old, new)
    assert notices
    assert all(who.contact is None for who, _ in notices)


@pytest.mark.defect("UP-9.5-04")
@pytest.mark.xfail(strict=True, reason="UP-9.5-04")
@pytest.mark.parametrize("base_url", ["javascript:alert(1)", "data:text/html,x"])
def test_regression_publish_rejects_non_http_base_urls(base_url: str) -> None:
    """Rejects a script or data scheme as the base URL instead of building a link on it.

    A `javascript:` link rendered as an anchor runs script in the viewer (audit 9.5).
    """
    with pytest.raises(SchemaError):
        publish(two_set_show(), "pub-1", base_url, AT)


@pytest.mark.defect("UP-9.5-05")
@pytest.mark.xfail(strict=True, reason="UP-9.5-05")
def test_regression_publish_requires_https_base_url() -> None:
    """Requires an https base URL (audit 9.5)."""
    with pytest.raises(SchemaError):
        publish(two_set_show(), "pub-1", "http://eight.example/p", AT)


@pytest.mark.defect("UP-9.5-06")
@pytest.mark.xfail(strict=True, reason="UP-9.5-06")
def test_regression_publish_does_not_mangle_a_base_url_query() -> None:
    """Rejects a query string in the base URL or keeps it after the path (audit 9.5)."""
    try:
        pub = publish(two_set_show(), "pub-1", "https://eight.example/p?ref=1", AT)
    except SchemaError:
        return
    assert pub.link == "https://eight.example/p/pub-1?ref=1"


@pytest.mark.defect("UP-9.5-07")
@pytest.mark.xfail(strict=True, reason="UP-9.5-07")
@pytest.mark.timeout(5, method="signal")
@pytest.mark.skipif(sys.platform == "win32", reason="signal timeout")
def test_regression_qr_code_caps_the_image_size() -> None:
    """Rejects a 100,000 px QR size instead of allocating a 10 GB image (audit 9.5; P0 #8).

    The address space is capped so a missing check fails fast with MemoryError.
    """
    pub = _published()
    with _address_space_cap(QR_MEMORY_CAP_BYTES), pytest.raises(SchemaError):
        qr_code(pub, size=HUGE_QR_SIZE)


@pytest.mark.defect("UP-9.5-08")
@pytest.mark.xfail(strict=True, reason="UP-9.5-08")
def test_regression_qr_code_wraps_an_overlong_link_error() -> None:
    """Raises a ShowPyError, not segno.DataOverflowError, for a link too long for a QR code."""
    pub = _published().model_copy(update={"link": f"https://eight.example/{OVERLONG_PATH}"})
    with pytest.raises(ShowPyError):
        qr_code(pub)


@pytest.mark.defect("UP-9.5-09")
@pytest.mark.xfail(strict=True, reason="UP-9.5-09")
def test_regression_qr_code_keeps_the_documented_error_level() -> None:
    """Renders at error level M as documented; segno silently boosts it to Q by default."""
    pub = _published()
    assert _module_grid(qr_code(pub), pub.link) == _reference_grid(pub.link, boost=False)


@pytest.mark.defect("UP-9.5-10")
@pytest.mark.xfail(strict=True, reason="UP-9.5-10")
def test_regression_pause_cannot_reopen_a_closed_publication() -> None:
    """Refuses to pause a closed publication instead of reopening it (audit 9.5 state machine)."""
    with pytest.raises(LockedError):
        pause(unpublish(_published()))


@pytest.mark.defect("UP-9.5-11")
@pytest.mark.xfail(strict=True, reason="UP-9.5-11")
def test_regression_republish_from_closed_does_not_go_live() -> None:
    """Refuses to republish a closed publication unless reopened explicitly (audit 9.5)."""
    closed = unpublish(_published())
    with pytest.raises(LockedError):
        publish(two_set_show(), "pub-1", BASE_URL, LATER, previous=closed)


@pytest.mark.defect("UP-9.5-12")
@pytest.mark.xfail(strict=True, reason="UP-9.5-12")
def test_regression_claim_spot_rejects_a_naive_time() -> None:
    """Rejects a claim time without a time zone (audit 9.5)."""
    show = two_set_show()
    with pytest.raises(SchemaError):
        claim_spot(_published(show), show, performer("Ada"), "T1", datetime(2026, 11, 12, 9, 0))


@pytest.mark.defect("UP-9.5-13")
@pytest.mark.xfail(strict=True, reason="UP-9.5-13")
def test_regression_claim_spot_rejects_a_time_before_publication() -> None:
    """Rejects a claim dated before the publication existed (audit 9.5)."""
    show = two_set_show()
    with pytest.raises(ValidationError):
        claim_spot(_published(show), show, performer("Ada"), "T1", EARLIER)


@pytest.mark.defect("UP-9.5-14")
@pytest.mark.xfail(strict=True, reason="UP-9.5-14")
def test_regression_reassign_rejects_a_naive_time() -> None:
    """Rejects a reassignment time without a time zone (audit 9.5)."""
    with pytest.raises(SchemaError):
        reassign(_published(), "T1", performer("Ada"), datetime(2026, 11, 12, 9, 0))


@pytest.mark.defect("UP-9.5-15")
@pytest.mark.xfail(strict=True, reason="UP-9.5-15")
def test_regression_reassign_rejects_a_time_before_publication() -> None:
    """Rejects a reassignment dated before the publication existed (audit 9.5)."""
    with pytest.raises(ValidationError):
        reassign(_published(), "T1", performer("Ada"), EARLIER)


def _reassign_checked(spot: str, who: Performer, show: Show) -> Publication:
    # Keyword `show` is the audit's fix: reassign must be told the show to validate against.
    return reassign(_published(show), spot, who, AT, show=show)  # type: ignore[call-arg]


@pytest.mark.defect("UP-9.5-16")
@pytest.mark.xfail(strict=True, reason="UP-9.5-16")
def test_regression_reassign_rejects_an_unknown_spot() -> None:
    """Refuses to move a performer onto a spot the show does not have (audit 9.5)."""
    with pytest.raises(UnknownRefError):
        _reassign_checked("T9", performer("Ada"), two_set_show())


@pytest.mark.defect("UP-9.5-17")
@pytest.mark.xfail(strict=True, reason="UP-9.5-17")
def test_regression_reassign_rejects_crew_on_a_performer_spot() -> None:
    """Refuses to put crew on a spot only performers may hold (audit 9.5)."""
    with pytest.raises(ValidationError):
        _reassign_checked("T1", performer("Cy", role="crew"), two_set_show())


@pytest.mark.defect("UP-9.5-18")
@pytest.mark.xfail(strict=True, reason="UP-9.5-18")
def test_regression_reassign_rejects_an_inactive_spot() -> None:
    """Refuses to move a performer onto an inactive spot (audit 9.5)."""
    with pytest.raises(ValidationError):
        _reassign_checked("T4", performer("Ada"), _crew_show())


@pytest.mark.defect("UP-9.5-19")
@pytest.mark.xfail(strict=True, reason="UP-9.5-19")
def test_regression_republish_after_a_spot_rename_keeps_the_claim() -> None:
    """Carries a claim over a spot rename given the mapping rewrite_spot_ids applies.

    Today the claim is dropped and the performer is told "spot removed" (audit 9.5).
    """
    show = two_set_show()
    pub = _with_claims(show, ("Ada", "T1"))
    renamed = rewrite_spot_ids(show, lambda spot: "X1" if spot == "T1" else spot)
    again = publish(
        renamed,
        "pub-1",
        BASE_URL,
        LATER,
        previous=pub,
        renames={"T1": "X1"},  # type: ignore[call-arg]
    )
    assert [(c.spot, c.performer.name) for c in again.claims] == [("X1", "Ada")]


@pytest.mark.defect("UP-9.5-20")
@pytest.mark.xfail(strict=True, reason="UP-9.5-20")
def test_regression_notify_changes_reports_a_deleted_set() -> None:
    """Tells a performer when a set they had a position in was deleted (audit 9.5)."""
    old = two_set_show()
    new = old.model_copy(update={"sets": old.sets[:1]})
    assert _notified(old, new)


@pytest.mark.defect("UP-9.5-21")
@pytest.mark.xfail(strict=True, reason="UP-9.5-21")
def test_regression_notify_changes_reports_a_new_set() -> None:
    """Tells a performer when a set was added after their publication (audit 9.5)."""
    old = two_set_show()
    third = old.sets[1].model_copy(update={"id": "3", "counts": 4.0})
    new = old.model_copy(update={"sets": (*old.sets, third)})
    assert _notified(old, new)


@pytest.mark.defect("UP-9.5-22")
@pytest.mark.xfail(strict=True, reason="UP-9.5-22")
def test_regression_notify_changes_reports_a_coordinate_system_change() -> None:
    """Tells performers when the coordinate system changes every printed position (audit 9.5)."""
    old = two_set_show()
    new = set_coordinates(old, CoordinateSystem(units="ft"))
    assert _notified(old, new)


@pytest.mark.defect("UP-9.5-23")
@pytest.mark.xfail(strict=True, reason="UP-9.5-23")
def test_regression_performer_view_needs_a_live_publication() -> None:
    """Refuses to build a view from a closed publication (audit 9.5)."""
    show = two_set_show()
    pub = unpublish(_with_claims(show, ("Ada", "T1")))
    with pytest.raises(LockedError):
        performer_view(pub, show, performer("Ada"))


@pytest.mark.defect("UP-9.5-24")
@pytest.mark.xfail(strict=True, reason="UP-9.5-24")
def test_regression_performer_view_checks_the_show_matches_the_publication() -> None:
    """Refuses a view built against a different show than the publication was made from."""
    show = two_set_show()
    pub = _with_claims(show, ("Ada", "T1"))
    other = show.model_copy(update={"id": "show-2"})
    with pytest.raises(ValidationError):
        performer_view(pub, other, performer("Ada"))


@pytest.mark.defect("UP-9.5-25")
@pytest.mark.xfail(strict=True, reason="UP-9.5-25")
def test_regression_performer_view_after_deletion_gives_a_performer_hint() -> None:
    """Gives a performer, not a designer, hint when their spot was deleted (audit 9.5)."""
    show = two_set_show()
    pub = _with_claims(show, ("Ada", "T2"))
    spots = tuple(s for s in show.cast.spots if s.id != "T2")
    sets = tuple(
        s.model_copy(update={"placements": tuple(p for p in s.placements if p.entity != "T2")})
        for s in show.sets
    )
    gone = show.model_copy(
        update={"sets": sets, "cast": show.cast.model_copy(update={"spots": spots})}
    )
    with pytest.raises(ShowPyError) as caught:
        performer_view(pub, gone, performer("Ada"))
    hint = caught.value.hint.lower()
    assert "designer" in hint
    assert "add the spot" not in hint


@pytest.mark.defect("UP-9.5-26")
@pytest.mark.xfail(strict=True, reason="UP-9.5-26")
def test_regression_publication_version_detects_concurrent_claims() -> None:
    """Bumps a monotonic version on every claim so two claims from one snapshot conflict.

    Without a version, two app instances claiming from the same snapshot both succeed and
    the conflict is undetectable (audit 9.5).
    """
    show = two_set_show()
    base = _published(show)
    first, _ = claim_spot(base, show, performer("Ada"), "T1", AT)
    second, _ = claim_spot(base, show, performer("Bo"), "T2", AT)
    assert first.version == base.version + 1  # type: ignore[attr-defined]
    assert second.version == first.version  # type: ignore[attr-defined]

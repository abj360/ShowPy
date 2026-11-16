#!/usr/bin/env python3

"""
test_publish.py --- tests publishing, QR codes, claims, designer actions, views and notices

Also holds the audited publishing defects (UP-9.5-01 to UP-9.5-26) as strict xfails.

Contains:
    test_regression_publish_link_carries_a_caller_supplied_join_token(): adds a join token
    test_regression_publication_snapshot_excludes_contact_details(): hides contacts
    test_regression_claims_and_notices_return_redacted_performers(): redacts contacts in claims
    test_regression_reassign_stores_performers_without_contact(): drops contact on reassign
    test_regression_republish_drops_contacts_held_by_a_legacy_publication(): strips carry-over
    test_regression_swap_claims_drops_contacts_held_by_a_legacy_publication(): strips on swap
    test_regression_readers_redact_contacts_of_a_legacy_publication(): redacts every reader
    test_regression_loading_a_legacy_publication_drops_contacts(): strips contacts on load
    test_regression_every_mutator_drops_contacts_of_a_legacy_publication(): strips on each edit
    test_regression_publish_rejects_a_short_or_malformed_join_token(): rejects weak or huge tokens
    test_regression_publish_rejects_base_urls_with_credentials_or_fragments(): rejects them
    test_regression_publish_rejects_unsafe_base_url(): rejects every malformed or ambiguous URL
    test_publish_accepts_well_formed_https_base_urls(): keeps valid hosts, ports and IPs
    test_regression_verify_join_token_returns_false_for_non_strings(): never raises on bad input
    test_regression_publication_repr_omits_the_join_token(): hides the link secret from repr
    test_regression_publish_errors_do_not_echo_the_join_token(): keeps the token out of errors
    test_regression_verify_join_token_checks_the_stored_hash(): accepts only the issued token
    test_regression_verify_join_token_fails_closed_without_a_hash(): rejects when none is stored
    test_regression_performer_view_url_omits_join_token(): keeps the token off the view URL
    test_regression_publication_rejects_a_malformed_token_hash(): validates the stored digest
    test_regression_publish_rejects_non_http_base_urls(): rejects script and data base URLs
    test_regression_publish_requires_https_base_url(): rejects an http base URL
    test_regression_publish_does_not_mangle_a_base_url_query(): keeps a base URL query intact
    test_regression_qr_code_caps_the_image_size(): rejects a QR size that needs gigabytes
    test_regression_qr_code_wraps_an_overlong_link_error(): wraps the QR overflow in a ShowPyError
    test_regression_qr_code_rejects_a_non_positive_or_non_integer_size(): rejects 0, negative, bool
    test_regression_qr_code_rejects_a_size_just_over_the_pixel_cap(): rejects 6,325 px
    test_regression_qr_code_overflow_error_hides_the_join_token(): keeps the token out of errors
    test_regression_qr_code_keeps_the_documented_error_level(): renders error level M as documented
    test_regression_pause_cannot_reopen_a_closed_publication(): refuses to pause a closed one
    test_regression_republish_from_closed_does_not_go_live(): refuses to republish a closed one
    test_regression_claim_spot_rejects_a_naive_time(): rejects a claim time without a time zone
    test_regression_claim_spot_rejects_a_time_before_publication(): rejects a claim too early
    test_regression_reassign_rejects_a_naive_time(): rejects a reassign time without a time zone
    test_regression_reassign_rejects_a_time_before_publication(): rejects a reassign too early
    test_regression_reassign_rejects_an_unknown_spot(): rejects a spot the show lacks
    test_regression_reassign_rejects_crew_on_a_performer_spot(): rejects crew on a performer spot
    test_regression_reassign_rejects_an_inactive_spot(): rejects an inactive spot
    test_regression_republish_after_a_spot_rename_keeps_the_claim(): carries claims over a rename
    test_regression_notify_changes_reports_a_deleted_set(): reports a deleted set
    test_regression_notify_changes_reports_a_new_set(): reports a new set
    test_regression_notify_changes_reports_a_coordinate_system_change(): reports a coordinate change
    test_regression_performer_view_needs_a_live_publication(): refuses a closed publication
    test_regression_performer_view_checks_the_show_matches_the_publication(): rejects a wrong show
    test_regression_performer_view_after_deletion_gives_a_performer_hint(): gives a performer hint
    test_regression_publication_version_detects_concurrent_claims(): bumps a version on each claim
    test_regression_stored_publication_holds_no_join_token(): keeps the token out of every output
    test_regression_join_link_round_trips_the_issued_token(): appends only the verified token
    test_regression_join_link_rejects_a_malformed_token(): refuses a token of the wrong shape
    test_regression_join_link_rejects_a_wrong_token(): refuses a token that does not match
    test_regression_join_link_fails_closed_without_a_digest(): refuses a v0.2.0 publication
    test_regression_qr_code_encodes_the_join_link(): encodes the verified joinable link
    test_regression_qr_code_rejects_a_wrong_token(): refuses to encode an unverified token
    test_regression_first_publish_requires_a_join_token(): refuses to publish without a token
    test_regression_republish_without_a_token_keeps_the_digest(): keeps the issued token valid
    test_regression_republish_with_a_token_rotates_the_digest(): revokes the old token
    test_regression_republish_without_a_digest_requires_a_token(): refuses a dead v0.2.0 link
    test_regression_loading_an_e1_publication_strips_the_join_token(): drops `?t=` on load
    test_regression_loading_an_e1_publication_keeps_other_query_parameters(): strips only `t`
    test_regression_invalid_e1_publication_errors_omit_the_join_token(): keeps errors clean
    test_publish_accepts_join_tokens_at_the_length_bounds(): accepts 22 and 256 characters
    test_publish_accepts_rfc3986_sub_delimiters_in_the_base_url_path(): keeps `~ ! $ & ( ) * + , ;
        = : @` in the path
"""

import hashlib
import io
import json
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any

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
from showpy.export.data import export_json
from showpy.export.models import Performer, Publication
from showpy.export.print_sheets import chart, coordinate_sheets
from showpy.export.publish import (
    claim_spot,
    claims,
    join_link,
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
    verify_join_token,
    who_is_on,
)
from showpy.sheet.coords import set_coordinates
from showpy.sheet.models import CoordinateSystem
from showpy.show.models import Show
from tests.fixtures.export_shows import AT, guided_show, performer
from tests.fixtures.motion_shows import with_rows, with_targets
from tests.fixtures.shows import two_set_show
from tests.helpers import fails_after

BASE_URL = "https://eight.example/p/"
JOIN_TOKEN = "Zk3Qz9-high-entropy_join-token"
OTHER_TOKEN = "Qp7Lm2-second_high-entropy-token"
SHORT_JOIN_TOKEN = "Zk3Qz9_join-token-22ch"
LINK = "https://eight.example/p/pub-1"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
LATER = AT + timedelta(minutes=5)
EARLIER = AT - timedelta(days=1)
QR_QUIET_MODULES = 4
DARK_MODULE, LIGHT_MODULE, ROW_BREAK = "#", ".", "|"
HUGE_QR_SIZE = 100_000
QR_MEMORY_CAP_BYTES = 1_500_000_000
OVERLONG_PATH = "a" * 8000
NAIVE_TIME = datetime(2026, 11, 12, 9, 0)  # noqa: DTZ001 - the tests check it is rejected
CONTACT = "ada@example.com"
DESIGNER_HINT = "check the spot id or add the spot first"


def _published(show: Show | None = None) -> Publication:
    return publish(show or two_set_show(), "pub-1", BASE_URL, AT, join_token=JOIN_TOKEN)


def _with_claims(show: Show, *pairs: tuple[str, str]) -> Publication:
    pub = publish(show, "pub-1", BASE_URL, AT, join_token=JOIN_TOKEN)
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
    assert pub.link == LINK
    assert (pub.status, pub.show_id, pub.published_at, pub.claims) == ("live", "show-1", AT, ())
    assert pub.revision


def test_publish_uses_the_stored_revision_and_is_deterministic() -> None:
    show = two_set_show().model_copy(update={"revision": "rev-9"})
    assert publish(show, "p", BASE_URL, AT, join_token=JOIN_TOKEN).revision == "rev-9"
    assert _published() == _published()


def test_publish_refuses_a_naive_time_or_blank_url() -> None:
    with pytest.raises(SchemaError):
        publish(two_set_show(), "p", BASE_URL, NAIVE_TIME, join_token=JOIN_TOKEN)
    with pytest.raises(SchemaError):
        publish(two_set_show(), "p", " ", AT, join_token=JOIN_TOKEN)


def test_pause_and_unpublish_return_new_publications() -> None:
    pub = _published()
    assert pause(pub).status == "paused"
    assert unpublish(pub).status == "closed"
    assert pub.status == "live"


def test_qr_code_is_a_png_of_the_requested_size() -> None:
    png = qr_code(_published(), JOIN_TOKEN, size=300)
    assert png.startswith(PNG_SIGNATURE)
    with Image.open(io.BytesIO(png)) as image:
        assert image.size == (300, 300)
        assert image.convert("L").getextrema() == (0, 255)
    assert qr_code(_published(), JOIN_TOKEN) == qr_code(_published(), JOIN_TOKEN)
    other = publish(two_set_show(), "pub-2", BASE_URL, AT, join_token=JOIN_TOKEN)
    assert qr_code(other, JOIN_TOKEN) != qr_code(_published(), JOIN_TOKEN)


def test_qr_code_refuses_a_size_too_small_for_the_code() -> None:
    with pytest.raises(SchemaError):
        qr_code(_published(), JOIN_TOKEN, size=10)


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
    again = publish(edited, "pub-1", BASE_URL, LATER, previous=pub, join_token=JOIN_TOKEN)
    assert [c.spot for c in again.claims] == ["T1"]
    assert again.published_at == LATER
    with pytest.raises(ValidationError):
        publish(edited, "pub-2", BASE_URL, LATER, previous=pub, join_token=JOIN_TOKEN)


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


def _grid_text(grid: list[list[bool]]) -> str:
    """Spells a module grid as one line, rows split by "|", for an exact comparison.

    Comparing the nested lists made pytest's CI-mode full diff run difflib's ndiff over every
    row; before Python 3.14 that took about a minute per failing comparison.
    """
    return ROW_BREAK.join(
        "".join(DARK_MODULE if dark else LIGHT_MODULE for dark in row) for row in grid
    )


def _claimed_with_contact() -> tuple[Show, Publication]:
    show = two_set_show()
    ada = performer("Ada").model_copy(update={"contact": CONTACT})
    pub, _ = claim_spot(_published(show), show, ada, "T1", AT)
    return show, pub


@contextmanager
def _address_space_cap(limit: int) -> Iterator[None]:
    import resource  # POSIX only; imported here so Windows can still collect this module

    soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    resource.setrlimit(resource.RLIMIT_AS, (limit, hard))
    try:
        yield
    finally:
        resource.setrlimit(resource.RLIMIT_AS, (soft, hard))


def _notified(old: Show, new: Show) -> list[tuple[Performer, str]]:
    return notify_changes(_with_claims(old, ("Ada", "T1")), old, new)


def test_regression_publish_link_carries_a_caller_supplied_join_token() -> None:
    """Puts a caller-supplied high-entropy token in the joinable link and stores only its hash.

    Contract: the raw token appears in `join_link(pub, token)` and nowhere in the stored
    publication; the SHA-256 hex digest of the token is stored so later joins can be checked
    against it (ADR 0018). The link is `base_url/<publication id>`, so anyone who guesses an ID
    can open the publication and claim with a self-declared performer (audit 9.5 security;
    P0 #10).
    """
    token = "k3Qz9-high-entropy-join-token"
    pub = publish(two_set_show(), "pub-1", BASE_URL, AT, join_token=token)
    assert token in join_link(pub, token)
    stored = pub.model_dump_json()
    assert token not in stored
    assert hashlib.sha256(token.encode()).hexdigest() in stored


def test_regression_publication_snapshot_excludes_contact_details() -> None:
    """Keeps performer contact data out of the serialized publication (audit 9.5; P0 #10).

    `Claim.performer.contact` serializes with the snapshot that is shared with every viewer.
    """
    _, pub = _claimed_with_contact()
    assert CONTACT not in pub.model_dump_json()


def test_regression_claims_and_notices_return_redacted_performers() -> None:
    """Returns performers without contact data from claims() and notify_changes() (audit 9.5)."""
    old, pub = _claimed_with_contact()
    new = with_targets({"T1": {"x": 3.0}}, show=old)
    assert all(c.performer.contact is None for c in claims(pub))
    notices = notify_changes(pub, old, new)
    assert notices
    assert all(who.contact is None for who, _ in notices)


def _require_contacts(stored: dict[str, Any]) -> None:
    claims_ = stored["claims"]
    assert claims_
    assert all(c["performer"]["contact"] == CONTACT for c in claims_)


def _legacy_publication() -> tuple[Show, Publication]:
    show, pub = _claimed_with_contact()
    stored = pub.model_dump(mode="json")
    stored["claims"][0]["performer"]["contact"] = CONTACT
    _require_contacts(stored)
    return show, Publication.model_validate(stored)


def test_regression_reassign_stores_performers_without_contact() -> None:
    """Drops the contact when the designer moves a performer onto a spot."""
    ada = performer("Ada").model_copy(update={"contact": CONTACT})
    pub = reassign(_published(), "T3", ada, AT)
    assert CONTACT not in pub.model_dump_json()


def test_regression_republish_drops_contacts_held_by_a_legacy_publication() -> None:
    """Strips a v0.2.0 contact when republishing carries its claim over."""
    show, legacy = _legacy_publication()
    pub = publish(show, "pub-1", BASE_URL, LATER, legacy, join_token=JOIN_TOKEN)
    assert [c.spot for c in pub.claims] == ["T1"]
    assert CONTACT not in pub.model_dump_json()


def test_regression_swap_claims_drops_contacts_held_by_a_legacy_publication() -> None:
    """Strips a v0.2.0 contact from claims rebuilt by a swap."""
    show, legacy = _legacy_publication()
    pub, _ = claim_spot(legacy, show, performer("Bo"), "T2", AT)
    assert CONTACT not in swap_claims(pub, "T1", "T2").model_dump_json()


def test_regression_readers_redact_contacts_of_a_legacy_publication() -> None:
    """Returns contact-free performers from every read path of a v0.2.0 publication."""
    show, legacy = _legacy_publication()
    assert all(c.performer.contact is None for c in claims(legacy))
    holder = who_is_on(legacy, "T1")
    assert holder is not None
    assert holder.contact is None
    view = performer_view(legacy, show, performer("Ada"))
    assert view.performer.contact is None
    assert CONTACT not in view.model_dump_json()
    new = with_targets({"T1": {"x": 3.0}}, show=show)
    assert all(who.contact is None for who, _ in notify_changes(legacy, show, new))


def _three_claim_legacy() -> tuple[Show, Publication]:
    show = two_set_show()
    stored = _with_claims(show, ("Ada", "T1"), ("Bo", "T2"), ("Cy", "T3")).model_dump(mode="json")
    for claim in stored["claims"]:
        claim["performer"]["contact"] = CONTACT
    _require_contacts(stored)
    return show, Publication.model_validate(stored)


def test_regression_loading_a_legacy_publication_drops_contacts() -> None:
    """Drops contacts at validation, so a v0.2.0 snapshot is clean as soon as it loads."""
    _, legacy = _three_claim_legacy()
    assert CONTACT not in legacy.model_dump_json()
    assert all(c.performer.contact is None for c in legacy.claims)


@pytest.mark.parametrize(
    "edit",
    [
        lambda show, pub: claim_spot(pub, show, performer("Di"), "T4", AT)[0],
        lambda _show, pub: reassign(pub, "T4", performer("Di"), AT),
        lambda _show, pub: release(pub, "T1"),
        lambda _show, pub: swap_claims(pub, "T1", "T2"),
        lambda _show, pub: pause(pub),
        lambda _show, pub: unpublish(pub),
        lambda show, pub: publish(show, "pub-1", BASE_URL, LATER, pub, join_token=JOIN_TOKEN),
    ],
    ids=["claim_spot", "reassign", "release", "swap_claims", "pause", "unpublish", "republish"],
)
def test_regression_every_mutator_drops_contacts_of_a_legacy_publication(
    edit: Callable[[Show, Publication], Publication],
) -> None:
    """Keeps every untouched legacy claim contact-free whichever edit runs."""
    show, legacy = _three_claim_legacy()
    assert CONTACT not in edit(show, legacy).model_dump_json()


@pytest.mark.parametrize("base_url", ["javascript:alert(1)", "data:text/html,x"])
def test_regression_publish_rejects_non_http_base_urls(base_url: str) -> None:
    """Rejects a script or data scheme as the base URL instead of building a link on it.

    A `javascript:` link rendered as an anchor runs script in the viewer (audit 9.5).
    """
    with pytest.raises(SchemaError):
        publish(two_set_show(), "pub-1", base_url, AT, join_token=JOIN_TOKEN)


def test_regression_publish_requires_https_base_url() -> None:
    """Requires an https base URL (audit 9.5)."""
    with pytest.raises(SchemaError):
        publish(two_set_show(), "pub-1", "http://eight.example/p", AT, join_token=JOIN_TOKEN)


def test_regression_publish_does_not_mangle_a_base_url_query() -> None:
    """Rejects a query string in the base URL or keeps it after the path (audit 9.5)."""
    try:
        pub = publish(
            two_set_show(), "pub-1", "https://eight.example/p?ref=1", AT, join_token=JOIN_TOKEN
        )
    except SchemaError:
        return
    assert pub.link == "https://eight.example/p/pub-1?ref=1"


@fails_after(5)
@pytest.mark.skipif(sys.platform != "linux", reason="RLIMIT_AS cap and signal timeout need Linux")
def test_regression_qr_code_caps_the_image_size() -> None:
    """Rejects a 100,000 px QR size instead of allocating a 10 GB image (audit 9.5; P0 #8).

    The address space is capped so a missing check fails fast with MemoryError.
    """
    pub = _published()
    with _address_space_cap(QR_MEMORY_CAP_BYTES), pytest.raises(SchemaError):
        qr_code(pub, JOIN_TOKEN, size=HUGE_QR_SIZE)


def test_regression_qr_code_wraps_an_overlong_link_error() -> None:
    """Raises a ShowPyError, not segno.DataOverflowError, for a link too long for a QR code."""
    pub = _published().model_copy(update={"link": f"https://eight.example/{OVERLONG_PATH}"})
    with pytest.raises(ShowPyError):
        qr_code(pub, JOIN_TOKEN)


@pytest.mark.parametrize("size", [0, -5, True, 1.5, "512"])
def test_regression_qr_code_rejects_a_non_positive_or_non_integer_size(size: Any) -> None:
    """Rejects a size that is zero, negative, a bool or not an int."""
    with pytest.raises(SchemaError):
        qr_code(_published(), JOIN_TOKEN, size=size)


def test_regression_qr_code_rejects_a_size_just_over_the_pixel_cap() -> None:
    """Rejects size 6,325 (40,005,625 pixels), one step over MAX_IMAGE_PIXELS."""
    with pytest.raises(SchemaError, match="QR code pixels"):
        qr_code(_published(), JOIN_TOKEN, size=6325)


def test_regression_qr_code_overflow_error_hides_the_join_token() -> None:
    """Keeps the joinable link, and so the join token, out of the overflow message and context."""
    pub = _published().model_copy(update={"link": f"https://eight.example/{OVERLONG_PATH}"})
    with pytest.raises(SchemaError) as caught:
        qr_code(pub, JOIN_TOKEN)
    assert JOIN_TOKEN not in str(caught.value)
    assert JOIN_TOKEN not in repr(caught.value.context)
    assert isinstance(caught.value.__cause__, segno.DataOverflowError)


def test_regression_qr_code_keeps_the_documented_error_level() -> None:
    """Renders at error level M as documented; segno silently boosts it to Q by default.

    The 45-character joinable link fits version 4 at level Q, so segno's default would boost.
    """
    pub = publish(two_set_show(), "p1", "https://x.example", AT, join_token=SHORT_JOIN_TOKEN)
    link = join_link(pub, SHORT_JOIN_TOKEN)
    boosted = _grid_text(_reference_grid(link, boost=True))
    expected = _grid_text(_reference_grid(link, boost=False))
    assert boosted != expected
    assert _grid_text(_module_grid(qr_code(pub, SHORT_JOIN_TOKEN), link)) == expected


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
        publish(two_set_show(), "pub-1", BASE_URL, LATER, previous=closed, join_token=JOIN_TOKEN)


@pytest.mark.defect("UP-9.5-12")
@pytest.mark.xfail(strict=True, reason="UP-9.5-12")
def test_regression_claim_spot_rejects_a_naive_time() -> None:
    """Rejects a claim time without a time zone (audit 9.5)."""
    show = two_set_show()
    with pytest.raises(SchemaError):
        claim_spot(_published(show), show, performer("Ada"), "T1", NAIVE_TIME)


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
        reassign(_published(), "T1", performer("Ada"), NAIVE_TIME)


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
        join_token=JOIN_TOKEN,
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
    """Gives a non-empty performer-facing hint, not the designer one, after deletion (audit 9.5)."""
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
    assert hint
    assert hint != DESIGNER_HINT
    assert "spot id" not in hint


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


@pytest.mark.parametrize(
    "token", ["", "short", "a" * 21, "a" * 22 + "!", "a" * 22 + "\n", "é" * 22, "a" * 257]
)
def test_regression_publish_rejects_a_short_or_malformed_join_token(token: str) -> None:
    """Rejects a join token under 22 characters or outside the base64url alphabet."""
    with pytest.raises(SchemaError):
        publish(two_set_show(), "pub-1", BASE_URL, AT, join_token=token)


@pytest.mark.parametrize(
    "base_url",
    [
        "https://user:pw@eight.example/p",
        "https://user@eight.example/p",
        "https://eight.example/p#frag",
        "https:///p",
        "https://eight.example/p?",
    ],
)
def test_regression_publish_rejects_base_urls_with_credentials_or_fragments(base_url: str) -> None:
    """Rejects userinfo, fragments, empty queries and missing hosts in the base URL."""
    with pytest.raises(SchemaError) as caught:
        publish(two_set_show(), "pub-1", base_url, AT, join_token=JOIN_TOKEN)
    assert "pw" not in str(caught.value.context)


def test_regression_publish_errors_do_not_echo_the_join_token() -> None:
    """Keeps the raw token out of every error message and context."""
    bad_token = "bad token with spaces and more"
    with pytest.raises(SchemaError) as caught:
        publish(two_set_show(), "pub-1", BASE_URL, AT, join_token=bad_token)
    assert bad_token not in f"{caught.value} {caught.value.context} {caught.value.hint}"


def test_regression_verify_join_token_checks_the_stored_hash() -> None:
    """Accepts only the token that was issued; a malformed one returns False."""
    pub = _published()
    assert pub.join_token_sha256 == hashlib.sha256(JOIN_TOKEN.encode()).hexdigest()
    assert verify_join_token(pub, JOIN_TOKEN)
    assert not verify_join_token(pub, "Zk3Qz9-high-entropy_join-tokeX")
    assert not verify_join_token(pub, "")
    assert not verify_join_token(pub, "\u00e9" * 30)


def test_regression_verify_join_token_fails_closed_without_a_hash() -> None:
    """Rejects every token when the publication stores no hash."""
    pub = _published().replace(join_token_sha256=None)
    assert not verify_join_token(pub, JOIN_TOKEN)


def test_regression_performer_view_url_omits_join_token() -> None:
    """Builds the path animation URL from the link without its query."""
    show = guided_show()
    pub = _with_claims(show, ("Ada", "T2"))
    view = performer_view(pub, show, performer("Ada"))
    assert JOIN_TOKEN not in view.path_animation_url
    assert view.path_animation_url == "https://eight.example/p/pub-1/spots/T2/path"


@pytest.mark.parametrize("digest", ["abc", "A" * 64, "g" * 64, "a" * 65])
def test_regression_publication_rejects_a_malformed_token_hash(digest: str) -> None:
    """Requires exactly 64 lowercase hex characters in the stored digest."""
    with pytest.raises(SchemaError):
        _published().replace(join_token_sha256=digest)


UNSAFE_BASE_URLS = [
    "https://eight.example:99999/p",
    "https://eight.example:80x/p",
    "https://eight.example:/p",
    "https://eight.example:0/p",
    "https://a.example\\.evil.example/p",
    "https://eight.example/p\\q",
    "https://eight .example/p",
    "https://eight.example/p q",
    "https://eight.example/p\x00",
    "https://eight.exam\tple/p",
    "https://eight.example/p\r\n",
    "https://\u00e9ight.example/p",
    "https://%61.example/p",
    "https://.",
    "https://./p",
    "https://a..b/p",
    "https://-a.example/p",
    "https://a-.example/p",
    "https://" + "a" * 64 + ".example/p",
    "https://" + ".".join(["a" * 60] * 5) + "/p",
    "https://999.1.1.1:x/p",
    "https://[::1/p",
    "https://[zz::1]/p",
    "https://eight.example/./p",
    "https://eight.example/p/../q",
    "https://eight.example/..",
    "https://eight.example/p?x=1",
    "https://eight.example/p#x",
    *[f"https://eight.example/p{char}q" for char in "\"<>'`{|}^"],
    "https://eight.example/p%zz",
    "https://u:p@eight.example/p",
    "http://eight.example/p",
    "",
]


@pytest.mark.parametrize("base_url", UNSAFE_BASE_URLS)
def test_regression_publish_rejects_unsafe_base_url(base_url: str) -> None:
    """Rejects ports, hosts, characters and path segments that urlsplit would alter or accept."""
    with pytest.raises(SchemaError) as caught:
        publish(two_set_show(), "pub-1", base_url, AT, join_token=JOIN_TOKEN)
    assert base_url not in str(caught.value.context) or not base_url


@pytest.mark.parametrize(
    ("base_url", "link_root"),
    [
        ("https://eight.example/p", "https://eight.example/p"),
        ("https://eight.example:8443/p/", "https://eight.example:8443/p"),
        ("https://[2001:db8::1]/p", "https://[2001:db8::1]/p"),
        ("https://192.0.2.1/p", "https://192.0.2.1/p"),
        ("https://eight.example/p%20q", "https://eight.example/p%20q"),
    ],
)
def test_publish_accepts_well_formed_https_base_urls(base_url: str, link_root: str) -> None:
    """Accepts DNS, IPv4 and bracketed IPv6 hosts, explicit ports and escaped paths."""
    pub = publish(two_set_show(), "pub-1", base_url, AT, join_token=JOIN_TOKEN)
    assert pub.link == f"{link_root}/pub-1"


@pytest.mark.parametrize("token", [None, b"Zk3Qz9-high-entropy_join-token", 123])
def test_regression_verify_join_token_returns_false_for_non_strings(token: object) -> None:
    """Returns False instead of raising when the token is not a string."""
    assert not verify_join_token(_published(), token)  # type: ignore[arg-type]


def test_regression_publication_repr_omits_the_join_token() -> None:
    """Keeps the link secret out of repr, which logs and tracebacks print."""
    assert JOIN_TOKEN not in repr(_published())


def _error_text(error: BaseException) -> str:
    """Spells every part of an error a log or traceback could print, its cause included."""
    parts = [str(error), repr(error), str(error.__cause__), repr(error.__cause__)]
    if isinstance(error, ShowPyError):
        parts += [error.hint, repr(error.context)]
    return " ".join(parts)


def test_regression_stored_publication_holds_no_join_token() -> None:
    """Keeps the raw token out of dumps, repr, exports, print sheets and join_link errors."""
    show = guided_show()
    pub = _with_claims(show, ("Ada", "T2"))
    outputs = [
        pub.model_dump_json(),
        json.dumps(pub.model_dump(mode="json")),
        repr(pub),
        str(pub),
        json.dumps(export_json(show)),
        *(sheet.model_dump_json() for sheet in coordinate_sheets(show, publication=pub)),
        chart(show, "2", pub).model_dump_json(),
        performer_view(pub, show, performer("Ada")).model_dump_json(),
    ]
    assert not [text for text in outputs if JOIN_TOKEN in text]
    for presented in (f"{JOIN_TOKEN}x", f"{JOIN_TOKEN}!", JOIN_TOKEN[:-1] + "X"):
        with pytest.raises(ShowPyError) as caught:
            join_link(pub, presented)
        assert JOIN_TOKEN not in _error_text(caught.value)
    with pytest.raises(ShowPyError) as caught:
        join_link(pub.replace(join_token_sha256=None), JOIN_TOKEN)
    assert JOIN_TOKEN not in _error_text(caught.value)


def test_regression_join_link_round_trips_the_issued_token() -> None:
    """Appends the issued token to the stored link in the format E1 used."""
    pub = _published()
    assert join_link(pub, JOIN_TOKEN) == f"{LINK}?t={JOIN_TOKEN}"
    assert verify_join_token(pub, join_link(pub, JOIN_TOKEN).rpartition("?t=")[2])


@pytest.mark.parametrize("token", ["", "short", "a" * 21, "a" * 257, f"{JOIN_TOKEN}!", None, 7])
def test_regression_join_link_rejects_a_malformed_token(token: Any) -> None:
    """Refuses a token outside 22 to 256 characters of `[A-Za-z0-9_-]`, or not a string."""
    with pytest.raises(SchemaError):
        join_link(_published(), token)


@pytest.mark.parametrize("token", [OTHER_TOKEN, JOIN_TOKEN[:-1] + "X", JOIN_TOKEN.upper()])
def test_regression_join_link_rejects_a_wrong_token(token: str) -> None:
    """Refuses a well-formed token whose digest is not the stored one."""
    with pytest.raises(ValidationError) as caught:
        join_link(_published(), token)
    assert token not in _error_text(caught.value)


def test_regression_join_link_fails_closed_without_a_digest() -> None:
    """Refuses every token for a publication that stores no digest, as v0.2.0 ones do."""
    with pytest.raises(ValidationError) as caught:
        join_link(_published().replace(join_token_sha256=None), JOIN_TOKEN)
    assert "join_token" in caught.value.hint


def test_regression_qr_code_encodes_the_join_link() -> None:
    """Encodes the verified joinable link, token included, at error level M."""
    pub = _published()
    link = join_link(pub, JOIN_TOKEN)
    expected = _grid_text(_reference_grid(link, boost=False))
    assert _grid_text(_module_grid(qr_code(pub, JOIN_TOKEN), link)) == expected


@pytest.mark.parametrize("token", [OTHER_TOKEN, "short"])
def test_regression_qr_code_rejects_a_wrong_token(token: str) -> None:
    """Refuses to encode a token that does not verify against the stored digest."""
    with pytest.raises(ShowPyError) as caught:
        qr_code(_published(), token)
    assert token not in _error_text(caught.value)


def test_regression_first_publish_requires_a_join_token() -> None:
    """Refuses a first publish without a join token, so no publication is born unjoinable."""
    with pytest.raises(SchemaError) as caught:
        publish(two_set_show(), "pub-1", BASE_URL, AT)
    assert "join_token" in caught.value.hint


def test_regression_republish_without_a_token_keeps_the_digest() -> None:
    """Keeps the issued token valid when a republish passes no token."""
    first = _published()
    again = publish(two_set_show(), "pub-1", BASE_URL, LATER, previous=first)
    assert again.join_token_sha256 == first.join_token_sha256
    assert join_link(again, JOIN_TOKEN) == f"{LINK}?t={JOIN_TOKEN}"


def test_regression_republish_with_a_token_rotates_the_digest() -> None:
    """Replaces the stored digest when a republish passes a new token, revoking the old one."""
    first = _published()
    again = publish(
        two_set_show(), "pub-1", BASE_URL, LATER, previous=first, join_token=OTHER_TOKEN
    )
    assert again.join_token_sha256 == hashlib.sha256(OTHER_TOKEN.encode()).hexdigest()
    assert join_link(again, OTHER_TOKEN) == f"{LINK}?t={OTHER_TOKEN}"
    with pytest.raises(ValidationError):
        join_link(again, JOIN_TOKEN)


def test_regression_republish_without_a_digest_requires_a_token() -> None:
    """Refuses to republish a digest-less v0.2.0 publication without issuing a token."""
    legacy = _published().replace(join_token_sha256=None)
    with pytest.raises(SchemaError):
        publish(two_set_show(), "pub-1", BASE_URL, LATER, previous=legacy)
    again = publish(
        two_set_show(), "pub-1", BASE_URL, LATER, previous=legacy, join_token=JOIN_TOKEN
    )
    assert verify_join_token(again, JOIN_TOKEN)


def _e1_stored(link: str) -> dict[str, Any]:
    """Builds a publication record shaped as E1-era code stored it, token in the link."""
    stored = _published().model_dump(mode="json")
    stored["link"] = link
    return stored


def test_regression_loading_an_e1_publication_strips_the_join_token() -> None:
    """Drops `?t=<token>` from an E1-era link on load and keeps the digest, so the token joins."""
    stored = _e1_stored(f"{LINK}?t={JOIN_TOKEN}")
    assert JOIN_TOKEN in json.dumps(stored)
    for loaded in (
        Publication.model_validate(stored),
        Publication.model_validate_json(json.dumps(stored)),
        Publication(**stored),
    ):
        assert loaded.link == LINK
        assert loaded.join_token_sha256 == hashlib.sha256(JOIN_TOKEN.encode()).hexdigest()
        assert JOIN_TOKEN not in f"{loaded.model_dump_json()} {loaded!r}"
        assert join_link(loaded, JOIN_TOKEN) == f"{LINK}?t={JOIN_TOKEN}"


@pytest.mark.parametrize(
    ("link", "expected"),
    [
        (f"{LINK}?ref=1&t={JOIN_TOKEN}", f"{LINK}?ref=1"),
        (f"{LINK}?t={JOIN_TOKEN}&ref=1&t={OTHER_TOKEN}", f"{LINK}?ref=1"),
        (f"{LINK}?t={JOIN_TOKEN}#top", f"{LINK}#top"),
        (f"{LINK}?t=", LINK),
        (f"{LINK}?", LINK),
        (LINK, LINK),
    ],
)
def test_regression_loading_an_e1_publication_keeps_other_query_parameters(
    link: str, expected: str
) -> None:
    """Strips every `t` parameter from a stored link and keeps the rest of it unchanged."""
    assert Publication.model_validate(_e1_stored(link)).link == expected


def test_regression_invalid_e1_publication_errors_omit_the_join_token() -> None:
    """Keeps the token out of the error when an E1-era record fails validation elsewhere.

    A missing field makes pydantic echo the whole input record, link included.
    """
    stored = _e1_stored(f"{LINK}?t={JOIN_TOKEN}")
    del stored["published_at"]
    with pytest.raises(SchemaError) as caught:
        Publication.model_validate(stored)
    assert JOIN_TOKEN not in _error_text(caught.value)


@pytest.mark.parametrize("token", ["a" * 22, "A1_-" * 64], ids=["22", "256"])
def test_publish_accepts_join_tokens_at_the_length_bounds(token: str) -> None:
    """Accepts tokens of exactly 22 and 256 characters, the bounds 21 and 257 fall outside."""
    pub = publish(two_set_show(), "pub-1", BASE_URL, AT, join_token=token)
    assert verify_join_token(pub, token)
    assert join_link(pub, token) == f"{LINK}?t={token}"


@pytest.mark.parametrize("char", list("~!$&()*+,;=:@"))
def test_publish_accepts_rfc3986_sub_delimiters_in_the_base_url_path(char: str) -> None:
    """Accepts each unreserved or sub-delimiter character RFC 3986 allows in a path segment."""
    base_url = f"https://eight.example/p{char}q"
    pub = publish(two_set_show(), "pub-1", base_url, AT, join_token=JOIN_TOKEN)
    assert pub.link == f"{base_url}/pub-1"
    assert join_link(pub, JOIN_TOKEN) == f"{base_url}/pub-1?t={JOIN_TOKEN}"

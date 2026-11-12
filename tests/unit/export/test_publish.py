#!/usr/bin/env python3

"""
test_publish.py --- tests publishing, QR codes, claims, designer actions, views and notices
"""

import io
from datetime import datetime, timedelta

import pytest
from PIL import Image

from stage.cast.models import Spot
from stage.choreo.models import PathRow
from stage.core.errors import LockedError, SchemaError, UnknownRefError, ValidationError
from stage.export.models import Publication
from stage.export.publish import (
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
from stage.show.models import Show
from tests.fixtures.export_shows import AT, guided_show, performer
from tests.fixtures.motion_shows import with_rows, with_targets
from tests.fixtures.shows import two_set_show

BASE_URL = "https://eight.example/p/"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
LATER = AT + timedelta(minutes=5)


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

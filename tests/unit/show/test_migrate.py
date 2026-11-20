#!/usr/bin/env python3

"""
test_migrate.py --- tests the stored format version, the migration chain and its report

Contains:
    test_version_of_reads_no_header_as_version_1(): treats a show.json without header as v1
    test_version_of_reads_the_current_header(): returns the version of a current header
    test_version_of_refuses_a_newer_version(): raises VersionError naming the version and newest
    test_version_of_refuses_a_bad_header(): raises UNKNOWN_FORMAT for a wrong format, half a
        header, a version 1 header, a boolean or a text version
    test_read_version_parses_show_json_strictly(): refuses a missing show.json, a non-object
        and non-strict JSON
    test_report_builder_groups_renames_by_kind_in_order(): keeps renames per kind in call order
    test_migration_report_is_read_only(): refuses edits to the renamed map
    test_sub_steps_run_in_the_fixed_order(): lists the JSON and model sub-steps in their order
    test_unfilled_sub_steps_change_nothing(): returns the documents and the show unchanged
    test_validation_after_ids_keeps_accepting_legacy_ids_until_ids_are_renamed(): passes the
        legacy-ID context to the last validation while the ids step renames nothing
    test_require_current_version_refuses_an_older_show_json(): raises OLD_FORMAT for v1
"""

import json

import pytest

from showpy.core.error_codes import ErrorCode
from showpy.core.errors import SchemaError, StorageError, VersionError
from showpy.core.ids import LEGACY_IDS_KEY
from showpy.core.json_types import JsonValue
from showpy.examples import two_set_show
from showpy.show.migrate import (
    CHECKED_CONTEXT,
    CURRENT_VERSION,
    FORMAT,
    LEGACY_CONTEXT,
    SHOW_DOC,
    MigrationReport,
    ReportBuilder,
    format_header,
    migrate_model,
    migrate_objects,
    read_version,
    require_current_version,
    version_of,
    without_header,
)
from showpy.show.migrations import V1_TO_V2_JSON_STEPS, V1_TO_V2_MODEL_STEPS
from showpy.show.migrations.v1_to_v2_ids import KEEPS_LEGACY_IDS

JSON_STEP_ORDER = [
    "migrate_forms",
    "migrate_paths",
    "migrate_selectors",
    "migrate_timeline",
    "migrate_facings",
    "migrate_sheet",
    "migrate_cast",
    "migrate_events",
]


def test_version_of_reads_no_header_as_version_1() -> None:
    assert version_of({"id": "show-1", "sets": []}, name=SHOW_DOC) == 1


def test_version_of_reads_the_current_header() -> None:
    assert format_header() == {"format": FORMAT, "version": CURRENT_VERSION}
    assert version_of({**format_header(), "id": "x"}, name=SHOW_DOC) == CURRENT_VERSION


def test_version_of_refuses_a_newer_version() -> None:
    with pytest.raises(VersionError) as caught:
        version_of({"format": FORMAT, "version": CURRENT_VERSION + 1}, name=SHOW_DOC)
    assert caught.value.code == ErrorCode.VERSION
    assert caught.value.context["version"] == CURRENT_VERSION + 1
    assert caught.value.context["newest"] == CURRENT_VERSION
    assert str(CURRENT_VERSION + 1) in str(caught.value)


@pytest.mark.parametrize(
    "header",
    [
        {"format": "stage.show", "version": 2},
        {"format": FORMAT},
        {"version": 2},
        {"format": FORMAT, "version": 1},
        {"format": FORMAT, "version": True},
        {"format": FORMAT, "version": "2"},
        {"format": FORMAT, "version": 2.0},
    ],
)
def test_version_of_refuses_a_bad_header(header: dict[str, object]) -> None:
    with pytest.raises(SchemaError) as caught:
        version_of(header, name=SHOW_DOC)
    assert caught.value.code == ErrorCode.UNKNOWN_FORMAT
    assert caught.value.context["path"] == SHOW_DOC


def test_read_version_parses_show_json_strictly() -> None:
    assert read_version({SHOW_DOC: b'{"id": "x"}'}) == 1
    assert read_version({SHOW_DOC: json.dumps(format_header()).encode()}) == CURRENT_VERSION
    with pytest.raises(StorageError):
        read_version({})
    with pytest.raises(SchemaError):
        read_version({SHOW_DOC: b"[]"})
    with pytest.raises(SchemaError) as caught:
        read_version({SHOW_DOC: b'{"format": "showpy.show", "format": "x", "version": 2}'})
    assert caught.value.code == ErrorCode.STRICT_JSON


def test_report_builder_groups_renames_by_kind_in_order() -> None:
    builder = ReportBuilder(1)
    builder.rename("form", "brass-2", "brass_2")
    builder.rename("set", "con", "con_")
    builder.rename("form", "brass-3", "brass_3")
    builder.repair("sorted tempo sections by count")
    report = builder.build()
    assert report == MigrationReport(
        from_version=1,
        renamed={
            "form": (("brass-2", "brass_2"), ("brass-3", "brass_3")),
            "set": (("con", "con_"),),
        },
        repairs=("sorted tempo sections by count",),
    )
    assert json.loads(report.model_dump_json())["renamed"]["set"] == [["con", "con_"]]


def test_migration_report_is_read_only() -> None:
    report = ReportBuilder(1).build()
    assert dict(report.renamed) == {}
    with pytest.raises(TypeError):
        report.renamed["spot"] = ()  # type: ignore[index]
    with pytest.raises(SchemaError):
        MigrationReport(from_version=0)


def test_sub_steps_run_in_the_fixed_order() -> None:
    assert [step.__name__ for step in V1_TO_V2_JSON_STEPS] == JSON_STEP_ORDER
    assert [step.__name__ for step in V1_TO_V2_MODEL_STEPS] == ["rename_ids"]


def test_unfilled_sub_steps_change_nothing() -> None:
    objects: dict[str, JsonValue] = {
        SHOW_DOC: {"id": "x", "sets": []},
        "/show/sheet.json": {"size_m": [1, 2]},
    }
    report = ReportBuilder(1)
    assert migrate_objects(json.loads(json.dumps(objects)), 1, report) == objects
    assert migrate_model(two_set_show(), 1, report) == two_set_show()
    assert report.build() == MigrationReport(from_version=1)
    assert migrate_objects(objects, CURRENT_VERSION, report) is objects


def test_validation_after_ids_keeps_accepting_legacy_ids_until_ids_are_renamed() -> None:
    assert LEGACY_CONTEXT == {LEGACY_IDS_KEY: True}
    assert KEEPS_LEGACY_IDS is True
    assert CHECKED_CONTEXT == LEGACY_CONTEXT


def test_require_current_version_refuses_an_older_show_json() -> None:
    require_current_version({**format_header(), "id": "x"}, name=SHOW_DOC)
    with pytest.raises(SchemaError) as caught:
        require_current_version({"id": "x"}, name=SHOW_DOC)
    assert caught.value.code == ErrorCode.OLD_FORMAT
    assert without_header({**format_header(), "id": "x"}) == {"id": "x"}

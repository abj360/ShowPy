#!/usr/bin/env python3

"""
validate.py --- reports structural problems in a timeline without raising

Contains:
    validate_timeline(): checks tempos, ramps, holds, markers, cues, measures and audio
"""

from collections import Counter
from collections.abc import Iterable
from typing import Literal

from stage.core.types import Count
from stage.core.validation import ValidationIssue, ValidationReport
from stage.time.models import TempoSection, Timeline, TimelineMode

type Level = Literal["error", "warning"]


def _issue(
    code: str, where: str, message: str, hint: str, level: Level = "error"
) -> ValidationIssue:
    return ValidationIssue(level=level, code=code, where=where, message=message, hint=hint)


def _duplicates(labels: Iterable[str]) -> list[str]:
    return sorted(label for label, seen in Counter(labels).items() if seen > 1)


def _in_show(tl: Timeline, c: Count) -> bool:
    return c >= 0 and (tl.total_counts is None or c <= tl.total_counts)


def _tempo_order_issues(tl: Timeline) -> list[ValidationIssue]:
    if not tl.tempos:
        if tl.mode is not TimelineMode.COUNTS:
            return []
        return [_issue("TEMPO_MISSING", "timeline", "counts mode has no tempo", "add a tempo")]
    issues = []
    if tl.tempos[0].at_count != 0.0:
        issues.append(
            _issue(
                "TEMPO_START",
                f"tempo at {tl.tempos[0].at_count:g}",
                "first tempo does not start at count 0",
                "add a tempo section at count 0",
            )
        )
    for before, after in zip(tl.tempos, tl.tempos[1:], strict=False):
        if after.at_count <= before.at_count:
            issues.append(
                _issue(
                    "TEMPO_ORDER",
                    f"tempo at {after.at_count:g}",
                    f"follows the section at {before.at_count:g}",
                    "keep tempo sections in increasing count order, one per count",
                )
            )
    return issues


def _section_issues(section: TempoSection, next_start: Count | None) -> list[ValidationIssue]:
    where = f"tempo at {section.at_count:g}"
    tempos = [section.counts_per_minute] + ([] if section.ramp_to is None else [section.ramp_to])
    if not all(t > 0 for t in tempos):
        return [_issue("TEMPO_NONPOSITIVE", where, "tempo is not positive", "use tempos > 0")]
    if (section.ramp_to is None) != (section.ramp_counts is None):
        return [
            _issue(
                "RAMP_INCOMPLETE",
                where,
                "ramp needs ramp_to and ramp_counts",
                "give both ramp fields or neither",
            )
        ]
    if section.ramp_counts is None:
        return []
    if not section.ramp_counts > 0:
        return [_issue("RAMP_LENGTH", where, "ramp has no length", "use ramp_counts > 0")]
    if next_start is not None and section.at_count + section.ramp_counts > next_start:
        return [
            _issue(
                "RAMP_CUT",
                where,
                f"ramp is cut short by the section at {next_start:g}",
                "shorten the ramp or move the next section",
                "warning",
            )
        ]
    return []


def _tempo_issues(tl: Timeline) -> list[ValidationIssue]:
    next_starts = [s.at_count for s in tl.tempos[1:]] + [None]
    section_issues = [
        issue
        for section, next_start in zip(tl.tempos, next_starts, strict=False)
        for issue in _section_issues(section, next_start)
    ]
    return _tempo_order_issues(tl) + section_issues


def _hold_issues(tl: Timeline) -> list[ValidationIssue]:
    issues = [
        _issue(
            "HOLD_NEGATIVE",
            f"hold at {h.at_count:g}",
            "hold has a negative count or length",
            "use a count >= 0 and seconds >= 0",
        )
        for h in tl.holds
        if h.at_count < 0 or h.seconds < 0
    ]
    counts = Counter(h.at_count for h in tl.holds)
    issues += [
        _issue(
            "HOLD_OVERLAP",
            f"hold at {at:g}",
            f"{seen} holds share one count",
            "merge them into one hold",
        )
        for at, seen in sorted(counts.items())
        if seen > 1
    ]
    return issues


def _marker_issues(tl: Timeline) -> list[ValidationIssue]:
    issues = [
        _issue(
            "MARKER_RANGE",
            f"marker {m.label!r}",
            f"count {m.at_count:g} is outside the show",
            f"use a count from 0 to {tl.total_counts:g}"
            if tl.total_counts is not None
            else "use a count >= 0",
        )
        for m in tl.markers
        if not _in_show(tl, m.at_count)
    ]
    issues += [
        _issue("MARKER_DUPLICATE", f"marker {label!r}", "label is used twice", "rename one")
        for label in _duplicates(m.label for m in tl.markers)
    ]
    return issues


def _cue_issues(tl: Timeline) -> list[ValidationIssue]:
    issues = [
        _issue(
            "CUE_NEGATIVE",
            f"cue {c.label!r}",
            "estimated duration is negative",
            "estimate 0 seconds or more",
        )
        for c in tl.cues
        if c.estimated_seconds < 0
    ]
    issues += [
        _issue("CUE_DUPLICATE", f"cue {label!r}", "label is used twice", "rename one")
        for label in _duplicates(c.label for c in tl.cues)
    ]
    return issues


def _audio_issues(tl: Timeline) -> list[ValidationIssue]:
    issues = [
        _issue(
            "AUDIO_RANGE",
            f"audio {t.label!r}",
            f"section {t.section} is not inside the show",
            "use a (start, end) section with start < end, both inside the show",
        )
        for t in tl.audio
        if t.section is not None
        and not (
            t.section[0] < t.section[1]
            and _in_show(tl, t.section[0])
            and _in_show(tl, t.section[1])
        )
    ]
    issues += [
        _issue("AUDIO_DUPLICATE", f"audio {label!r}", "label is used twice", "rename one")
        for label in _duplicates(t.label for t in tl.audio)
    ]
    return issues


def _measure_issues(tl: Timeline) -> list[ValidationIssue]:
    if tl.measures is None:
        return []
    lengths = [tl.measures.counts_per_measure, *(length for _, length in tl.measures.changes)]
    starts = [at for at, _ in tl.measures.changes]
    if all(length > 0 for length in lengths) and all(at >= 0 for at in starts):
        return []
    return [
        _issue(
            "MEASURE_LENGTH",
            "measures",
            "a measure length or change count is invalid",
            "use lengths > 0 and change counts >= 0",
        )
    ]


def validate_timeline(tl: Timeline) -> ValidationReport:
    """Checks a timeline and reports every structural problem as an issue, never raising.

    >>> from stage.time.timeline import new_timeline
    >>> validate_timeline(new_timeline(TimelineMode.COUNTS, 120.0)).ok
    True
    """
    checks = (
        _tempo_issues,
        _hold_issues,
        _marker_issues,
        _cue_issues,
        _measure_issues,
        _audio_issues,
    )
    return ValidationReport(issues=tuple(issue for check in checks for issue in check(tl)))

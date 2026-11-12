# stage.notes: design notes

Designers and the agent leave notes for rehearsal and for each other: "block locks in on
count 16", "watch T4 and T5 here". `stage.notes` stores them on the show, finds them by set,
transition or place, and lets an injected service draft suggestions.

## The data: models.py

An **anchor** says what a note is attached to. ADR 0001 fix 19 replaced an untagged union
(a string, a tuple or a polygon) with a tagged model, because an untagged union cannot be
read back from JSON without guessing. An `Anchor` has a `kind` and exactly one payload field:

| `kind`       | Payload field       | Constructor                        |
| ------------ | ------------------- | ---------------------------------- |
| `set`        | `set_id`            | `Anchor.on_set("2")`               |
| `transition` | `tid`               | `Anchor.on_transition(("1", "2"))` |
| `point`      | `point`             | `Anchor.at_point((0.0, 12.0))`     |
| `area`       | `area` (a `Region`) | `Anchor.on_area(region)`           |
| `spots`      | `spots`             | `Anchor.on_spots(["T4", "T5"])`    |

A `Note` has `id`, `text`, `kind` (`goal`, `idea`, `todo`, `warning`), `anchor`, `author`,
`created_at` and `visible`. A `DraftedNote` is what a drafter proposes: text, kind and anchor,
before it gets an ID and a time stamp.

```pycon
>>> from stage.notes.models import Anchor
>>> Anchor.on_spots(["T4", "T5"])
Anchor(kind='spots', set_id=None, tid=None, point=None, area=None, spots=('T4', 'T5'))
>>> Anchor(kind="set", set_id="2", point=(0.0, 0.0))
Traceback (most recent call last):
  ...
stage.core.errors.SchemaError: SCHEMA: set anchor needs only set_id (hint: use the Anchor.on_* constructors)
```

## Working with notes: notes.py

| Function                                                         | Does                                                                  |
| ---------------------------------------------------------------- | --------------------------------------------------------------------- |
| `add_note(show, text, anchor, note_id, at, author, kind="idea")` | adds a note; ID and time are explicit, so the result is deterministic |
| `edit_note(show, note_id, **changes)`                            | changes fields; the ID is fixed                                       |
| `remove_note(show, note_id)`                                     | removes a note                                                        |
| `notes_for(show, target, include_hidden=False)`                  | notes on a set (an ID) or a transition (a pair)                       |
| `notes_near(show, p, radius, set_id=None, include_hidden=False)` | notes at or around a point                                            |
| `draft_notes(show, concept, drafter, at)`                        | asks a `NoteDrafter` for suggestions; returns hidden notes            |
| `accept_draft(show, draft)`                                      | adds a drafted note, visible                                          |

`notes_near` matches by geometry: point anchors by distance, area anchors by distance to the
area, and spot anchors when any anchored spot's on-field placement (in any set, or only in
`set_id`) is within the radius.

`add_note` checks that the anchor's set, transition or spots exist, so a note can never point
at nothing.

```pycon
>>> from datetime import UTC, datetime
>>> from stage.examples import two_set_show
>>> from stage.notes.notes import add_note, notes_for, notes_near
>>> at = datetime(2026, 11, 11, 9, 0, tzinfo=UTC)
>>> show = add_note(two_set_show(), "go big", Anchor.on_set("2"), "n1", at, "pat", kind="goal")
>>> show = add_note(show, "lead here", Anchor.on_spots(["T1"]), "n2", at, "pat")
>>> [n.text for n in notes_for(show, "2")], [n.id for n in notes_near(show, (0.0, 9.0), 0.5)]
(['go big'], ['n2'])
>>> add_note(show, "x", Anchor.on_set("9"), "n3", at, "pat")
Traceback (most recent call last):
  ...
stage.core.errors.UnknownRefError: UNKNOWN_REF: unknown set '9' (hint: known sets: 1, 2)
>>> add_note(show, "dup", Anchor.on_set("1"), "n1", at, "pat")
Traceback (most recent call last):
  ...
stage.core.errors.SchemaError: SCHEMA: note 'n1' already exists (hint: pick a new note id, or use edit_note)
```

`T1` stands at `(0, 9)` in set 2, so the note anchored to it is found near that point.

### Drafting notes through an injected service

`draft_notes(show, concept, drafter, at)` turns a designer's concept ("calm opener, big
hit at 48") into suggested notes. The drafter is anything with a
`draft(show_summary, concept) -> list[DraftedNote]` method: in production an LLM client
behind a protocol, in tests a plain class. The library never calls a model itself (ADR 0001
fix 24). The drafts come back with IDs `draft-1`, `draft-2`, ... (skipping IDs in use),
author `agent`, the time `at` and `visible=False`. They are **not** added to the show; the
designer accepts them one by one with `accept_draft`. Malformed drafts, unknown anchors or
more than `MAX_DRAFTED_NOTES = 50` reject the whole batch.

```pycon
>>> from stage.notes.models import DraftedNote
>>> from stage.notes.notes import accept_draft, draft_notes
>>> class EchoDrafter:
...     def draft(self, show_summary, concept):
...         return [DraftedNote(text=concept, kind="goal", anchor=Anchor.on_set("1"))]
>>> drafts = draft_notes(show, "calm opener", EchoDrafter(), at)
>>> [(d.id, d.author, d.visible) for d in drafts], len(show.notes)
([('draft-1', 'agent', False)], 2)
>>> accepted = accept_draft(show, drafts[0])
>>> [(n.id, n.visible) for n in accepted.notes]
[('n1', True), ('n2', True), ('draft-1', True)]
```

> **Check your understanding 8.a.** Why must a note's `created_at` be passed in rather than
> read from the clock?

## Exercises

**Exercise 8.1 (warm-up).** Add a `todo` note "fix spacing" on transition `1→2` of
`two_set_show()` with ID `t1`, author `pat`, at 2026-11-11 10:00 UTC. Return the show.

**Exercise 8.2.** Add a point note at `(5, 7)` and return the IDs of notes within 1 m of
`(5.5, 7.5)` and within 0.5 m (as two lists).

**Exercise 8.3 (harder).** Write a drafter class whose `draft` returns one `warning` note per
transition of the show, anchored on that transition. Return the hidden drafts produced for
`two_set_show()`.

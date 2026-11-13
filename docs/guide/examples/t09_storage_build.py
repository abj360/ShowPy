#!/usr/bin/env python3

"""
t09_storage_build.py --- tutorial step 9: saves the show, meets a conflict, builds set 3 by script

Contains:
    SET_3_SCRIPT: holds the build script that returns set 3's arc form, now wider
    main(): saves, edits from two workspaces, writes a script, builds and lists the history
"""

import tempfile
from datetime import UTC, datetime, timedelta

from t05_paths import build_tutorial_show
from t08_notes_validate import add_notes

from stage.core.errors import ConflictError
from stage.show.build import build_set, stale
from stage.show.file_repository import FileRepository
from stage.show.history import changelog
from stage.show.manage import set_counts
from stage.show.storage import load_show, open_workspace, save_show

START = datetime(2026, 11, 11, 10, 0, tzinfo=UTC)
SET_3_SCRIPT = '''from stage.choreo.forms import arc_form


def build(ctx):
    """Puts the set 3 arc members on a wider arc of radius 16 m; the build places it."""
    members = ctx.show.forms["arc"].members
    ctx.log("arc rebuilt", radius=16.0, members=len(members))
    return [arc_form("arc", members, (0.0, 6.0), 16.0, 135.0, -90.0)]
'''


def minutes(n: int) -> datetime:
    """Returns the tutorial start time plus n minutes."""
    return START + timedelta(minutes=n)


def main() -> None:
    """Runs the storage and build workflow against a file repository in a temporary folder."""
    with tempfile.TemporaryDirectory() as folder:
        repo = FileRepository(folder)
        designer = open_workspace(repo, "opener", "designer")
        first = save_show(designer, add_notes(build_tutorial_show()), minutes(0), "create opener")
        print("saved revision", first.id[:7], "with", len(first.changed), "documents")

        agent = open_workspace(repo, "opener", "agent")
        slower = set_counts(load_show(designer), "3", 24.0, rescale=True)
        save_show(designer, slower, minutes(1), "slow the arc to 24 counts")
        try:
            save_show(agent, load_show(agent), minutes(2), "agent edit on a stale view")
        except ConflictError as error:
            print("conflict:", error.message)
            print("hint:", error.hint)
        agent.refresh()
        print(
            "agent now reads", agent.revision[:7], "set 3 counts", load_show(agent).set("3").counts
        )

        agent.write("/sets/3/set.py", SET_3_SCRIPT, at=minutes(3), message="script set 3")
        print("stale before build:", stale(agent))
        result = build_set(agent, "3", minutes(4))
        print("build logs:", list(result.logs))
        print("stale after build:", stale(agent))
        rebuilt = load_show(agent)
        t1 = rebuilt.set("3").get("T1")
        print(
            f"T1 now at ({t1.x:.3f}, {t1.y:.3f}), form radius",
            rebuilt.forms["arc"].geometry["curve"]["radius"],
        )
        print(changelog(agent).replace(folder, "<repo>"))


if __name__ == "__main__":
    main()

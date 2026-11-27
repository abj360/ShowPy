#!/usr/bin/env python3

"""
slot_race.py --- races processes to land one commit each on the same parent of a file repository

Spawned processes import their target by module name, so the racer lives here rather than in a
test module, which pytest imports under a generated name.

Contains:
    RACE_SHOW_ID: names the show every racer writes
    RACE_PATH: names the document every racer writes
    race_once_per_trial(): lands one commit per trial on its root and parent, reporting each result
"""

from datetime import UTC, datetime
from multiprocessing.queues import Queue
from multiprocessing.synchronize import Barrier
from pathlib import Path

from e8s.core.errors import ConflictError, E8sError
from e8s.show.document_repository import DocumentRepository
from e8s.show.file_backend import FileBackend

RACE_SHOW_ID = "race"
RACE_PATH = "/renders/race.txt"
RACE_AT = datetime(2026, 11, 24, tzinfo=UTC)
BARRIER_TIMEOUT_S = 60.0


def race_once_per_trial(
    barrier: Barrier,
    trials: list[tuple[str, str]],
    racer: int,
    results: "Queue[tuple[int, int, bool, str]]",
) -> None:
    """Lands one commit per trial after every racer reaches the barrier, reporting each result.

    Args:
        barrier: Releases every racer of a trial at once.
        trials: Repository root and expected parent of each trial.
        racer: This racer's number, written into its document so commits differ.
        results: Receives (trial, racer, has landed, revision ID landed or named as the head, or
            the code and message of any other error).
    """
    for trial, (root, parent) in enumerate(trials):
        repo = DocumentRepository(FileBackend(Path(root)))
        barrier.wait(BARRIER_TIMEOUT_S)
        try:
            landed = repo.transaction(
                RACE_SHOW_ID,
                {RACE_PATH: f"{trial} {racer}".encode()},
                expected_revision=parent,
                at=RACE_AT,
            )
        except ConflictError as error:
            results.put((trial, racer, False, str(error.context["head"])))
        except E8sError as error:
            results.put((trial, racer, False, f"{error.code}: {error.message}"))
        else:
            results.put((trial, racer, True, landed.id))

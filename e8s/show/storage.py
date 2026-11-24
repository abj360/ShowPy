"""
storage.py --- the repository and asset store protocols

Contains:
    ROOT_REVISION: names the revision before the first save
    Repository: versions a show's documents; every write checks the expected revision
    AssetStore: stores binary assets under content-hash URIs
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Protocol

from e8s.choreo.form_models import Form
from e8s.choreo.models import Set
from e8s.choreo.path_models import TransitionSpec
from e8s.core.ids import SetId
from e8s.show.models import Revision, Show
from e8s.time.models import Timeline

ROOT_REVISION = ""


class Repository(Protocol):
    """Versions each show's documents in a linear history of revisions.

    Paths are stored paths, which address sets by key (`/sets/<key>/set.json`).
    Every write takes the revision the caller read at and raises ConflictError, listing what
    landed since, when the head moved. Writes check validity, never pins or locks: edit
    functions enforce those for every caller; a host that writes raw documents
    owns them.
    """

    def shows(self) -> list[str]:
        """Returns the IDs of stored shows."""
        ...

    def head(self, show_id: str) -> str:
        """Returns the head revision ID, or ROOT_REVISION before the first save."""
        ...

    def revisions(self, show_id: str) -> list[Revision]:
        """Returns every revision, oldest first."""
        ...

    def get_revision(self, show_id: str, revision_id: str) -> Revision:
        """Returns one revision."""
        ...

    def paths(self, show_id: str, revision: str | None = None) -> list[str]:
        """Returns the stored document paths at a revision (default head), sorted."""
        ...

    def read(self, show_id: str, path: str, revision: str | None = None) -> bytes | None:
        """Returns a document at a revision (default head), or None when absent."""
        ...

    def documents(self, show_id: str, revision: str | None = None) -> Mapping[str, bytes]:
        """Returns the stored documents at a revision (default head), reading bytes on lookup."""
        ...

    def transaction(
        self,
        show_id: str,
        changes: Mapping[str, bytes | None],
        *,
        expected_revision: str,
        at: datetime,
    ) -> Revision:
        """Writes (bytes) or deletes (None) documents atomically as one revision.

        An edit that changes nothing returns the head revision unchanged (idempotent upsert).
        """
        ...

    def get_show(self, show_id: str, revision: str | None = None) -> Show:
        """Returns the whole show at a revision (default head)."""
        ...

    def put_show(
        self,
        show_id: str,
        show: Show,
        *,
        expected_revision: str,
        at: datetime,
    ) -> Revision:
        """Writes the whole show, deleting documents of sets and paths it no longer has.

        Checks validity only, never pins or locks; edit functions enforce those.
        """
        ...

    def list_sets(self, show_id: str) -> list[SetId]:
        """Returns set IDs in show order."""
        ...

    def get_set(self, show_id: str, set_id: str) -> Set:
        """Returns one set."""
        ...

    def put_set(
        self,
        show_id: str,
        new_set: Set,
        *,
        expected_revision: str,
        at: datetime,
    ) -> Revision:
        """Replaces one existing set.

        Checks validity only, never pins or locks; edit functions enforce those.
        """
        ...

    def get_script(self, show_id: str, set_id: str) -> str:
        """Returns a set's build script."""
        ...

    def put_script(
        self,
        show_id: str,
        set_id: str,
        source: str,
        *,
        expected_revision: str,
        at: datetime,
    ) -> Revision:
        """Replaces a set's build script.

        Checks validity only, never pins or locks; edit functions enforce those.
        """
        ...

    def get_paths(self, show_id: str, tid: tuple[str, str]) -> TransitionSpec:
        """Returns a transition's paths, defaulting to straight moves."""
        ...

    def put_paths(
        self,
        show_id: str,
        spec: TransitionSpec,
        *,
        expected_revision: str,
        at: datetime,
    ) -> Revision:
        """Replaces a transition's paths; counts follow the destination set.

        Checks validity only, never pins or locks; edit functions enforce those.
        """
        ...

    def get_forms(self, show_id: str, set_id: str) -> tuple[Form, ...]:
        """Returns a set's forms sorted by ID."""
        ...

    def put_forms(
        self,
        show_id: str,
        set_id: str,
        forms: tuple[Form, ...],
        *,
        expected_revision: str,
        at: datetime,
    ) -> Revision:
        """Replaces a set's forms.

        Checks validity only, never pins or locks; edit functions enforce those.
        """
        ...

    def get_timeline(self, show_id: str) -> Timeline:
        """Returns the show timeline."""
        ...

    def get_limits(self, show_id: str) -> str:
        """Returns the designer's limits YAML text; empty when none is stored."""
        ...

    def get_waivers(self, show_id: str) -> str:
        """Returns the designer's waivers YAML text; empty when none is stored."""
        ...


class AssetStore(Protocol):
    """Stores binary assets such as captured video and audio under content-hash URIs."""

    def put(self, data: bytes) -> str:
        """Stores bytes and returns their URI; storing equal bytes returns the same URI."""
        ...

    def get(self, uri: str) -> bytes:
        """Returns the bytes stored under a URI."""
        ...

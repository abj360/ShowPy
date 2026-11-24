"""
paths_store.py --- saves and loads a transition's paths through the workspace

Contains:
    save_paths(): writes a transition's paths document and returns the new revision id
    load_paths(): reads a transition's paths, defaulting to straight moves
"""

from datetime import datetime

from e8s.choreo.path_models import TransitionSpec
from e8s.show.documents import paths_doc
from e8s.show.show_io import load_show
from e8s.show.workspace import Workspace


def save_paths(ws: Workspace, spec: TransitionSpec, at: datetime) -> str:
    """Writes `/transitions/<a>-<b>/paths.json`; checks run through the tool hook, not here."""
    revision = ws.write(paths_doc(spec.id), spec._dumped_json(), at=at)
    return revision.id


def load_paths(ws: Workspace, tid: tuple[str, str]) -> TransitionSpec:
    """Reads a transition's paths; a transition without a document moves straight."""
    return load_show(ws).transition(tid)

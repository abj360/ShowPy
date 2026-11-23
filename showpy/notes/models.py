"""
models.py --- the public names of the note models; serves Anchor's old path with a warning

The models are defined in the private module showpy.show._notes_document, which the v1
migration keeps using after these public names leave the library in 1.0.0.

Contains:
    Note: re-exports the model of one design note
    NotesDocument: re-exports the model of the notes document kept beside a show
    NoteKind: re-exports the kinds a note can have
    __getattr__(): returns Anchor from showpy.show.models and warns that it moved
"""

from showpy.core.deprecation import moved
from showpy.show._notes_document import Note as Note
from showpy.show._notes_document import NoteKind as NoteKind
from showpy.show._notes_document import NotesDocument as NotesDocument

__getattr__ = moved(
    __name__, {"Anchor": "showpy.show.models.Anchor"}, since="0.4.0", remove="0.6.0"
)

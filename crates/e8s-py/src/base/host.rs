//! host --- gives non-ASCII text the answers of Python's own Unicode tables
//!
//! Contains:
//!     PyText: answers the core's Unicode questions by asking Python's `str` and `unicodedata`
//!     read_text(): reads a Python string, replacing a lone surrogate with U+FFFD
//!     repr_of(): returns the Python `repr` of any object

use std::borrow::Cow;
use std::cell::RefCell;
use std::collections::HashMap;

use e8s_core::base::text::{StdText, TextHost};
use pyo3::prelude::*;
use pyo3::sync::PyOnceLock;
use pyo3::types::PyString;

const NFC_FORM: &str = "NFC";
/// The most characters the class memo holds, so hostile text cannot grow it without bound.
const MEMO_CHARS: usize = 4096;
static NORMALIZE: PyOnceLock<Py<PyAny>> = PyOnceLock::new();

thread_local! {
    /// Remembers Python's answers about each non-ASCII character: its tables never change.
    static CLASSES: RefCell<HashMap<char, (bool, bool)>> = RefCell::new(HashMap::new());
}

/// Answers the core's Unicode questions for non-ASCII text by asking Python.
///
/// A failed call falls back to Rust's own tables, which the core's `StdText` holds.
pub struct PyText<'py> {
    py: Python<'py>,
}

impl<'py> PyText<'py> {
    /// Creates a host that asks the interpreter behind `py`.
    pub const fn new(py: Python<'py>) -> Self {
        Self { py }
    }

    fn ask(&self, text: &str, method: &str) -> Option<Bound<'py, PyAny>> {
        PyString::new(self.py, text).call_method0(method).ok()
    }

    fn ask_text(&self, text: &str, method: &str) -> Option<String> {
        self.ask(text, method)?.extract().ok()
    }

    fn ask_flag(&self, c: char, method: &str) -> Option<bool> {
        self.ask(c.encode_utf8(&mut [0; 4]), method)?.extract().ok()
    }

    /// Returns whether Python calls the character alphanumeric and whether it calls it space.
    fn classify(&self, c: char) -> (bool, bool) {
        let known = CLASSES.with(|classes| classes.borrow().get(&c).copied());
        if let Some(answer) = known {
            return answer;
        }
        let answer = (
            self.ask_flag(c, "isalnum")
                .unwrap_or_else(|| StdText.is_alnum(c)),
            self.ask_flag(c, "isspace")
                .unwrap_or_else(|| StdText.is_space(c)),
        );
        CLASSES.with(|classes| {
            let mut classes = classes.borrow_mut();
            if classes.len() < MEMO_CHARS {
                classes.insert(c, answer);
            }
        });
        answer
    }
}

impl TextHost for PyText<'_> {
    fn nfc(&self, text: &str) -> String {
        let normalized = NORMALIZE
            .import(self.py, "unicodedata", "normalize")
            .and_then(|normalize| normalize.call1((NFC_FORM, text)))
            .and_then(|result| result.extract());
        normalized.unwrap_or_else(|_| StdText.nfc(text))
    }

    fn casefold(&self, text: &str) -> String {
        self.ask_text(text, "casefold")
            .unwrap_or_else(|| StdText.casefold(text))
    }

    fn repr(&self, text: &str) -> String {
        PyString::new(self.py, text)
            .repr()
            .map(|shown| shown.to_string_lossy().into_owned())
            .unwrap_or_else(|_| StdText.repr(text))
    }

    fn is_alnum(&self, c: char) -> bool {
        self.classify(c).0
    }

    fn is_space(&self, c: char) -> bool {
        self.classify(c).1
    }
}

/// Reads a Python string; a lone surrogate, which has no UTF-8 form, becomes U+FFFD.
pub fn read_text<'a>(text: &'a Bound<'_, PyString>) -> Cow<'a, str> {
    text.to_string_lossy()
}

/// Returns the Python `repr` of any object, or its type name when `repr` itself fails.
pub fn repr_of(object: &Bound<'_, PyAny>) -> String {
    object.repr().map_or_else(
        |_| {
            object
                .get_type()
                .name()
                .map_or_else(|_| "?".to_owned(), |name| name.to_string())
        },
        |shown| shown.to_string_lossy().into_owned(),
    )
}

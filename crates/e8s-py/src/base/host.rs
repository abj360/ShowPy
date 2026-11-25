//! host --- gives non-ASCII text the answers of Python's own Unicode tables
//!
//! Contains:
//!     PyText: answers the core's Unicode questions by asking Python's `str` and `unicodedata`
//!     run(): runs one core call with a Python host, raising Python's own error if a question failed
//!     read_text(): reads a Python string, replacing a lone surrogate with U+FFFD
//!     repr_of(): returns the Python `repr` of any object

use std::borrow::Cow;
use std::cell::RefCell;
use std::collections::HashMap;
use std::panic::AssertUnwindSafe;

use e8s_core::base::text::TextHost;
use e8s_core::base::{CoreError, CoreResult, ErrorCode, ErrorKind};
use pyo3::intern;
use pyo3::prelude::*;
use pyo3::types::PyString;

use crate::errors::{guard, loaded, to_py_err};

/// The most characters the class memo holds, so hostile text cannot grow it without bound.
const MEMO_CHARS: usize = 4096;

thread_local! {
    /// Remembers Python's answers about each non-ASCII character: its tables never change.
    static CLASSES: RefCell<HashMap<char, (bool, bool)>> = RefCell::new(HashMap::new());
}

/// Answers the core's Unicode questions for non-ASCII text by asking Python.
///
/// A question that raises in Python is stored, and the core call stops with a placeholder
/// error; `run` then raises the stored Python exception, so nothing is ever guessed.
pub struct PyText<'py> {
    py: Python<'py>,
    failure: RefCell<Option<PyErr>>,
}

fn host_failed() -> CoreError {
    CoreError::new(
        ErrorKind::Schema,
        ErrorCode::Schema,
        "Python could not answer a Unicode question",
        "see the Python error that caused this one",
    )
}

impl<'py> PyText<'py> {
    /// Creates a host that asks the interpreter behind `py`.
    pub const fn new(py: Python<'py>) -> Self {
        Self {
            py,
            failure: RefCell::new(None),
        }
    }

    fn settle<T>(&self, answer: PyResult<T>) -> CoreResult<T> {
        answer.map_err(|err| {
            let mut slot = self.failure.borrow_mut();
            if slot.is_none() {
                *slot = Some(err);
            }
            host_failed()
        })
    }

    fn ask_class(&self, c: char) -> PyResult<(bool, bool)> {
        let text = PyString::new(self.py, c.encode_utf8(&mut [0; 4]));
        let alnum = text.call_method0(intern!(self.py, "isalnum"))?.extract()?;
        let space = text.call_method0(intern!(self.py, "isspace"))?.extract()?;
        Ok((alnum, space))
    }

    /// Returns whether Python calls the character alphanumeric and whether it calls it space.
    fn classify(&self, c: char) -> CoreResult<(bool, bool)> {
        if let Some(known) = CLASSES.with(|classes| classes.borrow().get(&c).copied()) {
            return Ok(known);
        }
        let answer = self.settle(self.ask_class(c))?;
        CLASSES.with(|classes| {
            let mut classes = classes.borrow_mut();
            if classes.len() < MEMO_CHARS {
                classes.insert(c, answer);
            }
        });
        Ok(answer)
    }
}

impl TextHost for PyText<'_> {
    fn nfc(&self, text: &str) -> CoreResult<String> {
        let asked = loaded(self.py, "unicodedata")
            .and_then(|module| {
                let form = intern!(self.py, "NFC");
                module.call_method1(intern!(self.py, "normalize"), (form, text))
            })
            .and_then(|normalized| normalized.extract());
        self.settle(asked)
    }

    fn casefold(&self, text: &str) -> CoreResult<String> {
        let asked = PyString::new(self.py, text)
            .call_method0(intern!(self.py, "casefold"))
            .and_then(|folded| folded.extract());
        self.settle(asked)
    }

    fn repr(&self, text: &str) -> CoreResult<String> {
        let asked = PyString::new(self.py, text)
            .repr()
            .map(|shown| shown.to_string_lossy().into_owned());
        self.settle(asked)
    }

    fn is_alnum(&self, c: char) -> CoreResult<bool> {
        Ok(self.classify(c)?.0)
    }

    fn is_space(&self, c: char) -> CoreResult<bool> {
        Ok(self.classify(c)?.1)
    }
}

/// Runs one core call with a Python host under the error boundary.
///
/// # Errors
///
/// Returns the Python exception a Unicode question raised, else the e8s exception of the core's
/// error, else the one `guard` builds for a panic.
pub fn run<T>(
    py: Python<'_>,
    name: &'static str,
    call: impl FnOnce(&PyText<'_>) -> CoreResult<T>,
) -> PyResult<T> {
    let host = PyText::new(py);
    let outcome = guard(name, AssertUnwindSafe(|| Ok(call(&host))))?;
    outcome.map_err(|err| {
        let stored = host.failure.borrow_mut().take();
        stored.unwrap_or_else(|| to_py_err(py, &err))
    })
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

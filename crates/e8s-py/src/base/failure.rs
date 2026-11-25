//! failure --- raises core errors from the base bindings without importing per error
//!
//! Contains:
//!     guarded(): runs one core call under the error boundary, building its error from cached classes
//!     exception_of(): builds the e8s exception a core error names from cached classes

use std::panic::UnwindSafe;

use e8s_core::base::{ContextValue, CoreError, CoreResult, ErrorCode, ErrorKind};
use pyo3::prelude::*;
use pyo3::sync::PyOnceLock;
use pyo3::types::PyDict;

use crate::errors::guard;

static ERRORS: PyOnceLock<Py<PyAny>> = PyOnceLock::new();
static CODES: PyOnceLock<Py<PyAny>> = PyOnceLock::new();

/// Runs one core call under the error boundary and raises its error as an e8s exception.
///
/// A panic still becomes CORE_PANIC through `guard`; a `CoreError` becomes the same exception
/// `guard` would raise, with the module lookups cached instead of repeated for every error.
///
/// # Errors
///
/// Returns the e8s exception for the core's error, or the one `guard` builds for a panic.
pub fn guarded<T>(
    name: &'static str,
    call: impl FnOnce() -> CoreResult<T> + UnwindSafe,
) -> PyResult<T> {
    guard(name, || Ok(call()))?.map_err(|err| Python::attach(|py| exception_of(py, &err)))
}

/// Builds the e8s exception a core error names, with its code, message, context and hint.
///
/// Returns the import or construction error instead when the exception cannot be built.
pub fn exception_of(py: Python<'_>, err: &CoreError) -> PyErr {
    build(py, err).unwrap_or_else(|failure| failure)
}

fn build(py: Python<'_>, err: &CoreError) -> PyResult<PyErr> {
    let errors = ERRORS.import(py, "e8s.core.errors", "__dict__")?;
    let class = errors.get_item(err.kind().class_name())?;
    let context = PyDict::new(py);
    for (key, value) in err.context() {
        match value {
            ContextValue::Bool(flag) => context.set_item(key, *flag),
            ContextValue::Int(number) => context.set_item(key, *number),
            ContextValue::Float(number) => context.set_item(key, *number),
            ContextValue::Text(text) => context.set_item(key, text),
        }?;
    }
    let kwargs = PyDict::new(py);
    kwargs.set_item("hint", err.hint())?;
    kwargs.set_item("context", context)?;
    if !is_default_code(err) {
        let codes = CODES.import(py, "e8s.core.error_codes", "ErrorCode")?;
        kwargs.set_item("code", codes.getattr(err.code().as_str())?)?;
    }
    Ok(PyErr::from_value(
        class.call((err.message(),), Some(&kwargs))?,
    ))
}

/// Tells whether the error's code is the default of its class, so it need not be passed.
const fn is_default_code(err: &CoreError) -> bool {
    matches!(
        (err.kind(), err.code()),
        (ErrorKind::Schema, ErrorCode::Schema) | (ErrorKind::ZeroLength, ErrorCode::ZeroLength)
    )
}

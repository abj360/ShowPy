//! errors --- the binding's error boundary: core errors and panics become e8s exceptions
//!
//! Contains:
//!     PANIC_HINT: the hint of every CORE_PANIC error
//!     guard(): runs one core call, raising its error, or a panic it caught, as an e8s exception
//!     to_py_err(): builds the e8s exception a core error names
//!     add_probes(): adds the private boundary hook `_finite_probe` to a module

use std::any::Any;
use std::cell::{Cell, RefCell};
use std::panic::{self, PanicHookInfo, UnwindSafe};
use std::sync::Once;

use e8s_core::base::{ContextValue, CoreError, CoreResult, ErrorCode, ErrorKind, require_finite};
use pyo3::prelude::*;
use pyo3::types::PyDict;

/// The hint of every CORE_PANIC error.
pub const PANIC_HINT: &str = "This is a bug in e8s; report it with the function name.";
const ERRORS_MODULE: &str = "e8s.core.errors";
const CODES_MODULE: &str = "e8s.core.error_codes";
const CODE_ENUM: &str = "ErrorCode";
const UNKNOWN_PAYLOAD: &str = "a panic with no message";

thread_local! {
    /// Counts the guarded calls running on this thread, so the hook knows a panic is caught.
    static GUARD_DEPTH: Cell<usize> = const { Cell::new(0) };
    /// Holds the message and location of the last panic caught on this thread.
    static CAUGHT_PANIC: RefCell<Option<String>> = const { RefCell::new(None) };
}

static QUIET_HOOK: Once = Once::new();

/// Runs one core call and raises its error, or a panic it caught, as an e8s exception.
///
/// A `CoreError` becomes the class its kind names, with the same code, message, context and
/// hint. An unwinding panic becomes `E8sError` with code `CORE_PANIC`, context
/// `{"function": name}` and `PANIC_HINT`, never `PanicException`. The panic's message and
/// location go into the error's message instead of onto stderr.
///
/// # Errors
///
/// Returns the e8s exception for the core's error or the caught panic; if building that
/// exception fails (e8s cannot be imported), returns the import error instead.
pub fn guard<T>(name: &'static str, f: impl FnOnce() -> CoreResult<T> + UnwindSafe) -> PyResult<T> {
    install_quiet_hook();
    GUARD_DEPTH.with(|depth| depth.set(depth.get() + 1));
    let outcome = panic::catch_unwind(f);
    GUARD_DEPTH.with(|depth| depth.set(depth.get().saturating_sub(1)));
    outcome
        .unwrap_or_else(|payload| Err(panic_error(name, payload.as_ref())))
        .map_err(|err| Python::attach(|py| to_py_err(py, &err)))
}

/// Builds the e8s exception a core error names, with the same code, message, context and hint.
///
/// Returns the import or construction error instead when the exception cannot be built.
pub fn to_py_err(py: Python<'_>, err: &CoreError) -> PyErr {
    build_exception(py, err).unwrap_or_else(|failure| failure)
}

fn build_exception(py: Python<'_>, err: &CoreError) -> PyResult<PyErr> {
    let class = py.import(ERRORS_MODULE)?.getattr(err.kind().class_name())?;
    let code = py
        .import(CODES_MODULE)?
        .getattr(CODE_ENUM)?
        .getattr(err.code().as_str())?;
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
    kwargs.set_item("code", code)?;
    Ok(PyErr::from_value(
        class.call((err.message(),), Some(&kwargs))?,
    ))
}

/// Turns a caught panic into the CORE_PANIC error of the guarded function.
fn panic_error(name: &'static str, payload: &(dyn Any + Send)) -> CoreError {
    let detail = CAUGHT_PANIC
        .with(|caught| {
            caught
                .try_borrow_mut()
                .ok()
                .and_then(|mut slot| slot.take())
        })
        .unwrap_or_else(|| payload_text(payload).to_owned());
    CoreError::new(
        ErrorKind::E8s,
        ErrorCode::CorePanic,
        format!("{name} panicked: {detail}"),
        PANIC_HINT,
    )
    .with_context("function", name)
}

fn payload_text(payload: &(dyn Any + Send)) -> &str {
    if let Some(text) = payload.downcast_ref::<&str>() {
        return text;
    }
    payload
        .downcast_ref::<String>()
        .map_or(UNKNOWN_PAYLOAD, String::as_str)
}

/// Installs, once, a panic hook that records a guarded panic instead of printing it.
///
/// A panic outside any guard still reaches the previous hook, so it prints as before.
fn install_quiet_hook() {
    QUIET_HOOK.call_once(|| {
        let previous = panic::take_hook();
        panic::set_hook(Box::new(move |info| {
            if GUARD_DEPTH.with(Cell::get) == 0 {
                previous(info);
                return;
            }
            let report = panic_report(info);
            CAUGHT_PANIC.with(|caught| {
                if let Ok(mut slot) = caught.try_borrow_mut() {
                    *slot = Some(report);
                }
            });
        }));
    });
}

fn panic_report(info: &PanicHookInfo<'_>) -> String {
    let text = payload_text(info.payload());
    match info.location() {
        Some(location) => format!("{text} at {}:{}", location.file(), location.line()),
        None => text.to_owned(),
    }
}

/// Returns `value` when it is finite and raises SchemaError otherwise, as every core boundary
/// does with its input numbers.
#[pyfunction(name = "_finite_probe")]
fn finite_probe(value: f64) -> PyResult<f64> {
    guard("_finite_probe", || require_finite("value", value))
}

/// Adds the private boundary hook `_finite_probe` to a module.
///
/// # Errors
///
/// Returns the error of adding the function.
pub fn add_probes(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(finite_probe, module)?)
}

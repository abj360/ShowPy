//! errors --- the binding's error boundary: core errors and panics become e8s exceptions
//!
//! Contains:
//!     PANIC_HINT: the hint of every CORE_PANIC error
//!     PANIC_DETAIL_CHARS: caps the panic payload a CORE_PANIC message repeats
//!     guard(): runs one core call, raising its error, or a panic it caught, as an e8s exception
//!     to_py_err(): builds the e8s exception a core error names
//!     loaded(): finds a loaded module by its current entry in `sys.modules`
//!     add_probes(): adds the private boundary hook `_finite_probe` to a module

use std::any::Any;
use std::cell::{Cell, RefCell};
use std::panic::{self, PanicHookInfo, UnwindSafe};
use std::sync::Once;

use e8s_core::base::{ContextValue, CoreError, CoreResult, ErrorCode, ErrorKind, require_finite};
use pyo3::intern;
use pyo3::prelude::*;
use pyo3::sync::PyOnceLock;
use pyo3::types::{PyDict, PyString};

/// The hint of every CORE_PANIC error.
pub const PANIC_HINT: &str = "This is a bug in e8s; report it with the function name.";
const ERRORS_MODULE: &str = "e8s.core.errors";
/// The interpreter's table of loaded modules, kept so no raised error imports anything.
static MODULES: PyOnceLock<Py<PyAny>> = PyOnceLock::new();
const CODES_MODULE: &str = "e8s.core.error_codes";
const CODE_ENUM: &str = "ErrorCode";
/// Caps the characters of the panic payload a CORE_PANIC message repeats.
pub const PANIC_DETAIL_CHARS: usize = 200;
const UNKNOWN_PAYLOAD: &str = "a panic with no message";
const CUT_MARK: &str = "...";

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

/// Finds a loaded module by its current entry in `sys.modules`, importing it when absent.
///
/// A reload or a re-import changes the entry, so the classes and the enum are always the
/// current ones.
pub fn loaded<'py>(py: Python<'py>, name: &str) -> PyResult<Bound<'py, PyAny>> {
    match MODULES
        .import(py, "sys", "modules")?
        .get_item(PyString::intern(py, name))
    {
        Ok(module) => Ok(module),
        Err(_) => Ok(py.import(name)?.into_any()),
    }
}

fn build_exception(py: Python<'_>, err: &CoreError) -> PyResult<PyErr> {
    let class =
        loaded(py, ERRORS_MODULE)?.getattr(PyString::intern(py, err.kind().class_name()))?;
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
    kwargs.set_item(intern!(py, "hint"), err.hint())?;
    kwargs.set_item(intern!(py, "context"), context)?;
    if !has_default_code(&class, err)? {
        let code = loaded(py, CODES_MODULE)?
            .getattr(CODE_ENUM)?
            .getattr(PyString::intern(py, err.code().as_str()))?;
        kwargs.set_item(intern!(py, "code"), code)?;
    }
    Ok(PyErr::from_value(
        class.call((err.message(),), Some(&kwargs))?,
    ))
}

/// Tells whether the error's code is the class's own `default_code`, which the class applies
/// when no code is passed; the default is read from the Python class, never copied here.
fn has_default_code(class: &Bound<'_, PyAny>, err: &CoreError) -> PyResult<bool> {
    let default = class.getattr(intern!(class.py(), "default_code"))?;
    Ok(default.extract::<&str>()? == err.code().as_str())
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
        .unwrap_or_else(|| cut(payload_text(payload)));
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

/// Describes a panic by its payload, cut to PANIC_DETAIL_CHARS, and its source file and line.
///
/// Only the file's name is kept: the full path is the build machine's, not the user's.
fn panic_report(info: &PanicHookInfo<'_>) -> String {
    let text = cut(payload_text(info.payload()));
    match info.location() {
        Some(location) => format!(
            "{text} at {}:{}",
            file_name(location.file()),
            location.line()
        ),
        None => text,
    }
}

fn cut(text: &str) -> String {
    match text.char_indices().nth(PANIC_DETAIL_CHARS) {
        Some((end, _)) => format!("{}{CUT_MARK}", &text[..end]),
        None => text.to_owned(),
    }
}

fn file_name(path: &str) -> &str {
    path.rsplit(['/', '\\']).next().unwrap_or(path)
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
    crate::add_hook(module, wrap_pyfunction!(finite_probe, module)?.as_any())
}

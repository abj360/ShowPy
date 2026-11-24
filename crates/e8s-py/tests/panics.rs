//! panics --- proves that core errors and panics reach Python as e8s exceptions
//!
//! Contains:
//!     panic_in_a_bound_call_raises_e8s_error: a panic in a guarded call raises CORE_PANIC
//!     core_error_raises_the_class_of_its_kind: a core error keeps its class and fields

#![allow(clippy::panic, reason = "the tests panic on purpose inside the guard")]

use _core::errors::{PANIC_HINT, guard};
use e8s_core::base::{CoreError, CoreResult, ErrorCode, ErrorKind};
use pyo3::panic::PanicException;
use pyo3::prelude::*;
use pyo3::types::PyDict;

/// Puts the checkout and its uv environment on `sys.path`, so `e8s.core.errors` imports.
///
/// The embedded interpreter starts from the base Python that pyo3 linked, not the virtual
/// environment, so it reads the environment's `site-packages` (and its `.pth` files) here.
const IMPORT_E8S: &std::ffi::CStr = cr#"
import os, site, sys, sysconfig
venv = os.environ.get("VIRTUAL_ENV") or os.path.join(root, ".venv")
site.addsitedir(sysconfig.get_path("purelib", vars={"base": venv, "platbase": venv}))
sys.path.insert(0, root)
"#;

fn import_e8s(py: Python<'_>) -> PyResult<()> {
    let root = concat!(env!("CARGO_MANIFEST_DIR"), "/../..");
    let locals = PyDict::new(py);
    locals.set_item("root", root)?;
    py.run(IMPORT_E8S, None, Some(&locals))
}

fn error_of<T>(result: PyResult<T>) -> PyResult<PyErr> {
    match result {
        Ok(_) => Err(PyErr::new::<pyo3::exceptions::PyAssertionError, _>(
            "the call succeeded",
        )),
        Err(err) => Ok(err),
    }
}

#[test]
fn panic_in_a_bound_call_raises_e8s_error() -> PyResult<()> {
    Python::attach(|py| {
        import_e8s(py)?;
        let err = error_of(guard("probe", || -> CoreResult<()> { panic!("boom") }))?;
        let e8s_error = py.import("e8s.core.errors")?.getattr("E8sError")?;
        assert!(!err.is_instance_of::<PanicException>(py));
        assert!(err.get_type(py).is(&e8s_error));
        let value = err.value(py);
        assert_eq!(value.getattr("code")?.str()?.to_str()?, "CORE_PANIC");
        assert_eq!(value.getattr("hint")?.extract::<String>()?, PANIC_HINT);
        let context = value.getattr("context")?;
        assert_eq!(context.len()?, 1);
        assert_eq!(context.get_item("function")?.extract::<String>()?, "probe");
        let message = value.getattr("message")?.extract::<String>()?;
        assert!(message.starts_with("probe panicked: boom at "), "{message}");
        Ok(())
    })
}

#[test]
fn core_error_raises_the_class_of_its_kind() -> PyResult<()> {
    Python::attach(|py| {
        import_e8s(py)?;
        let failure = CoreError::new(ErrorKind::Timeline, ErrorCode::TempoOrder, "late", "sort")
            .with_context("count", 3_i64)
            .with_context("ramp", true);
        let err = error_of(guard("probe", move || -> CoreResult<()> { Err(failure) }))?;
        let timeline_error = py.import("e8s.core.errors")?.getattr("TimelineError")?;
        assert!(err.get_type(py).is(&timeline_error));
        let value = err.value(py);
        assert_eq!(value.getattr("code")?.str()?.to_str()?, "TEMPO_ORDER");
        assert_eq!(value.getattr("message")?.extract::<String>()?, "late");
        assert_eq!(value.getattr("hint")?.extract::<String>()?, "sort");
        let context = value.getattr("context")?;
        assert_eq!(context.get_item("count")?.extract::<i64>()?, 3);
        assert!(context.get_item("ramp")?.extract::<bool>()?);
        Ok(())
    })
}

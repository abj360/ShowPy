//! e8s-py --- PyO3 bindings that maturin builds into the extension module `e8s._core`
//!
//! Contains:
//!     base: the bindings of the base area, which fill `e8s._core.core`
//!     cache: one converted core value per frozen part, keyed by the part's identity
//!     errors: the error boundary that turns core errors and panics into e8s exceptions
//!     AREAS: names each submodule of `e8s._core` with its docstring
//!     add_hook(): adds a private hook to a module without listing it in `__all__`
//!     _core(): initializes `e8s._core` with its version, one submodule per area and the
//!         private hooks of `e8s._core.core`

#![forbid(unsafe_code)]

pub mod base;
pub mod cache;
pub mod errors;

use pyo3::intern;
use pyo3::prelude::*;
use pyo3::types::PyString;
// Linked before the first port calls them, so the lock pins the core and the array crate.
use e8s_core as _;
use numpy as _;

/// Names each submodule of `e8s._core` with the docstring it carries.
const AREAS: [(&str, &str); 7] = [
    (
        "core",
        "Compiled units, identifiers, errors and encodings of e8s.core.",
    ),
    (
        "geo",
        "Compiled curves, shapes, regions, spacing and transforms of e8s.geo.",
    ),
    ("time", "Compiled count timeline and clock of e8s.time."),
    (
        "sheet",
        "Compiled sheet templates, coordinate systems and bounds of e8s.sheet.",
    ),
    (
        "cast",
        "Compiled spots, groups and selection grammar of e8s.cast.",
    ),
    ("engine", "Compiled path engine of e8s.choreo."),
    ("forms", "Compiled forms and matching of e8s.choreo."),
];

/// The area whose submodule also holds the binding's private hooks.
const CORE_AREA: &str = "core";

/// Adds a private hook to a module by its own name, keeping it out of the module's `__all__`.
///
/// # Errors
///
/// Returns the error of reading the function's name or setting the attribute.
pub fn add_hook(module: &Bound<'_, PyModule>, function: &Bound<'_, PyAny>) -> PyResult<()> {
    module.setattr(
        function
            .getattr(intern!(module.py(), "__name__"))?
            .cast::<PyString>()?,
        function,
    )
}

/// Initializes `e8s._core`: its `__version__`, one submodule per area and the private hooks.
///
/// Each submodule goes into `sys.modules` under its dotted name, so `import e8s._core.time`
/// works although an extension module is not a package.
#[pymodule]
fn _core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    let py = m.py();
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    let modules = py.import("sys")?.getattr("modules")?;
    for (area, doc) in AREAS {
        let name = format!("e8s._core.{area}");
        let submodule = PyModule::new(py, &name)?;
        submodule.setattr("__doc__", doc)?;
        if area == CORE_AREA {
            errors::add_probes(&submodule)?;
            cache::add_probes(&submodule)?;
            base::add_all(&submodule)?;
        }
        m.add(area, &submodule)?;
        modules.set_item(name, &submodule)?;
    }
    Ok(())
}

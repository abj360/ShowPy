//! e8s-py --- PyO3 bindings that maturin builds into the extension module `e8s._core`
//!
//! Contains:
//!     AREAS: names each submodule of `e8s._core` with its docstring
//!     _core(): initializes `e8s._core` with its version and one submodule per area

#![forbid(unsafe_code)]

use pyo3::prelude::*;
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

/// Initializes `e8s._core`: its `__version__` and one submodule per area.
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
        m.add(area, &submodule)?;
        modules.set_item(name, &submodule)?;
    }
    Ok(())
}

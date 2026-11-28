//! base --- bindings of the base area: numbers, caps, angle helpers, IDs and references
//!
//! Contains:
//!     canonical: binds canonical values, canonical JSON bytes and content hashes
//!     host: gives non-ASCII text the answers of Python's own Unicode tables
//!     numeric: binds number text, angle wrapping, rounding and the size caps
//!     rng: binds the seeded PCG64 generator for its tests
//!     identifiers: binds the ID checks, references and transition IDs
//!     strict: binds the strict JSON reader and the nesting count
//!     walk: walks Python values into the canonical JSON model
//!     add_all(): adds every public function and constant of the base area to a module

mod canonical;
mod host;
mod identifiers;
mod numeric;
mod rng;
mod strict;
mod walk;

use e8s_core::base::{caps, ids, numbers, rounding, units};
use pyo3::prelude::*;
use pyo3::types::PyFrozenSet;

/// Adds every public function and constant of the base area to the `e8s._core.core` module.
///
/// # Errors
///
/// Returns the error of adding any of them.
pub fn add_all(module: &Bound<'_, PyModule>) -> PyResult<()> {
    numeric::add_functions(module)?;
    identifiers::add_functions(module)?;
    canonical::add_functions(module)?;
    strict::add_functions(module)?;
    rng::add_hooks(module)?;
    add_constants(module)
}

fn add_constants(module: &Bound<'_, PyModule>) -> PyResult<()> {
    let integers = [
        ("MAX_POINTS", caps::MAX_POINTS),
        ("MAX_TABLE", caps::MAX_TABLE),
        ("MAX_GRID_CELLS", caps::MAX_GRID_CELLS),
        ("MAX_RANGE", caps::MAX_RANGE),
        ("MAX_DEPTH", caps::MAX_DEPTH),
        ("MAX_TEXT_CHARS", caps::MAX_TEXT_CHARS),
        ("MAX_COUNTS", caps::MAX_COUNTS),
        ("MAX_SPOTS", caps::MAX_SPOTS),
        ("MAX_SETS", caps::MAX_SETS),
        ("MAX_EXTENSION_BYTES", caps::MAX_EXTENSION_BYTES),
    ];
    for (name, value) in integers {
        module.add(name, value)?;
    }
    module.add("TURN_DEG", numbers::TURN_DEG)?;
    module.add("DEFAULT_DECIMALS", numbers::DEFAULT_DECIMALS)?;
    module.add("MAX_DECIMALS", numbers::MAX_DECIMALS)?;
    module.add("DEFAULT_STEP", rounding::DEFAULT_STEP)?;
    module.add("ZERO_VECTOR_TOL", units::ZERO_VECTOR_TOL)?;
    module.add("MIN_LAYOUT_HEIGHT_M", caps::MIN_LAYOUT_HEIGHT_M)?;
    module.add("MAX_COORD_M", caps::MAX_COORD_M)?;
    module.add("MIN_TEMPO", caps::MIN_TEMPO)?;
    module.add("ID_MAX_LENGTH", ids::ID_MAX_LENGTH)?;
    module.add("ID_PATTERN_SOURCE", ids::ID_PATTERN)?;
    module.add("SHEET_ID_PATTERN_SOURCE", ids::SHEET_ID_PATTERN)?;
    module.add("VERSION_PATTERN_SOURCE", ids::VERSION_PATTERN)?;
    module.add(
        "RESERVED_IDS",
        PyFrozenSet::new(module.py(), ids::RESERVED_IDS)?,
    )
}

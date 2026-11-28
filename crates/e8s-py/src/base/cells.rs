//! cells --- binds close-name suggestions and the CSV formula guard
//!
//! Contains:
//!     close_matches(): suggests known names close to a misspelled one
//!     neutralize_cell(): prefixes a formula-looking cell with a quote
//!     add_functions(): adds the functions above and the CSV constants to a module

use e8s_core::base::csv_cells::{self, FORMULA_GUARD, FORMULA_TRIGGERS, PLAIN_DECIMAL};
use e8s_core::base::similar;
use pyo3::intern;
use pyo3::prelude::*;
use pyo3::sync::PyOnceLock;
use pyo3::types::{PyList, PyString, PyTuple};

use super::host::{code_points_of, text_bytes};
use crate::errors::guard;

static TRIGGERS: PyOnceLock<Py<PyTuple>> = PyOnceLock::new();
static DECIMAL_PATTERN: PyOnceLock<Py<PyAny>> = PyOnceLock::new();

fn triggers(py: Python<'_>) -> PyResult<&Bound<'_, PyTuple>> {
    Ok(TRIGGERS
        .get_or_try_init(py, || PyTuple::new(py, FORMULA_TRIGGERS).map(Bound::unbind))?
        .bind(py))
}

fn decimal_pattern(py: Python<'_>) -> PyResult<&Bound<'_, PyAny>> {
    Ok(DECIMAL_PATTERN
        .get_or_try_init(py, || {
            let re = py.import("re")?;
            let pattern = re.call_method1("compile", (PLAIN_DECIMAL, re.getattr("ASCII")?))?;
            Ok::<_, PyErr>(pattern.unbind())
        })?
        .bind(py))
}

/// Suggests up to three known names close to `name`, best first, for error hints.
///
/// >>> from e8s._core.core import close_matches
/// >>> close_matches("T3", ["T1", "B7"])
/// ['T1']
#[pyfunction]
fn close_matches<'py>(
    name: &Bound<'py, PyString>,
    candidates: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyList>> {
    let items = candidates
        .try_iter()?
        .collect::<PyResult<Vec<Bound<'py, PyAny>>>>()?;
    let target = code_points_of(name)?;
    let pool = items
        .iter()
        .map(|item| code_points_of(item.cast::<PyString>()?))
        .collect::<PyResult<Vec<_>>>()?;
    let picked = guard("close_matches", || {
        Ok(similar::close_matches(&target, &pool))
    })?;
    PyList::new(name.py(), picked.into_iter().map(|index| &items[index]))
}

/// Runs v0.4.0's own steps on a cell that is not a string, so the object's errors rise.
fn neutralize_object<'py>(cell: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    let py = cell.py();
    let starts = cell.call_method1(intern!(py, "startswith"), (triggers(py)?,))?;
    if !starts.is_truthy()?
        || !decimal_pattern(py)?
            .call_method1(intern!(py, "fullmatch"), (cell,))?
            .is_none()
    {
        return Ok(cell.clone());
    }
    PyString::new(py, FORMULA_GUARD).add(cell)
}

/// Prefixes a cell that starts like a formula and is not a plain decimal with a quote.
///
/// Args:
/// cell: Text of one CSV cell.
///
/// Returns:
/// safe_cell: The cell unchanged, or the cell behind `'` so spreadsheets show it as text.
///
/// >>> from e8s._core.core import neutralize_cell
/// >>> neutralize_cell("=1+1"), neutralize_cell("-5"), neutralize_cell("cue")
/// ("'=1+1", '-5', 'cue')
#[pyfunction]
fn neutralize_cell<'py>(cell: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    let Ok(text) = cell.cast::<PyString>() else {
        return neutralize_object(cell);
    };
    let bytes = text_bytes(text)?;
    if !guard("neutralize_cell", || Ok(csv_cells::needs_guard(&bytes)))? {
        return Ok(cell.clone());
    }
    PyString::new(cell.py(), FORMULA_GUARD).add(cell)
}

/// Adds the functions of this module and the CSV constants to `module`.
///
/// # Errors
///
/// Returns the error of building a constant or adding any of them.
pub fn add_functions(module: &Bound<'_, PyModule>) -> PyResult<()> {
    let py = module.py();
    module.add_function(wrap_pyfunction!(close_matches, module)?)?;
    module.add_function(wrap_pyfunction!(neutralize_cell, module)?)?;
    module.add("FORMULA_TRIGGERS", triggers(py)?)?;
    module.add("FORMULA_GUARD", FORMULA_GUARD)?;
    module.add("PLAIN_DECIMAL", decimal_pattern(py)?)
}

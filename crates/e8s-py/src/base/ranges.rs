//! ranges --- binds natural sorting, ID splitting, range expansion and run compaction
//!
//! Contains:
//!     natural_key(): returns the sort key that puts T2 before T10
//!     natural_sort(): sorts IDs so T2 comes before T10
//!     split_id(): splits an ID into its prefix and trailing number
//!     parse_range(): expands `T1-T8` into IDs
//!     format_ranges(): compacts IDs into `T1-T4, T7`
//!     add_functions(): adds the functions above to a module

use std::borrow::Cow;

use e8s_core::base::natural::{self, Chunk};
use e8s_core::base::ranges;
use pyo3::exceptions::PyTypeError;
use pyo3::prelude::*;
use pyo3::types::{PyInt, PyList, PyString, PyTuple};

use super::host::{run, text_bytes, text_from_bytes};
use crate::errors::{guard, py_int};

const ASCII_DIGITS: &str = "0123456789";
/// Digit runs this short always fit 64 bits.
const SHORT_DIGITS: usize = 18;
const NUMBER_KIND: i64 = 0;
const TEXT_KIND: i64 = 1;

/// Returns `text` as a string; for any other object, first calls the method v0.4.0 called on
/// it, so that call's own error rises, then refuses the object with TypeError.
fn text_or_error<'py>(
    text: &Bound<'py, PyAny>,
    method: &str,
    argument: Option<&str>,
) -> PyResult<Bound<'py, PyString>> {
    if let Ok(string) = text.cast::<PyString>() {
        return Ok(string.clone());
    }
    match argument {
        Some(argument) => text.call_method1(method, (argument,))?,
        None => text.call_method0(method)?,
    };
    Err(PyTypeError::new_err(format!(
        "expected str, got {}",
        text.get_type().name()?
    )))
}

fn strings<'py>(ids: &Bound<'py, PyAny>) -> PyResult<Vec<Bound<'py, PyString>>> {
    ids.try_iter()?
        .map(|item| Ok(item?.cast_into::<PyString>()?))
        .collect()
}

fn all_bytes<'a>(texts: &'a [Bound<'_, PyString>]) -> PyResult<Vec<Cow<'a, [u8]>>> {
    texts.iter().map(text_bytes).collect()
}

fn number<'py>(py: Python<'py>, digits: &[u8]) -> PyResult<Bound<'py, PyAny>> {
    let text = String::from_utf8_lossy(digits);
    if digits.len() <= SHORT_DIGITS
        && let Ok(value) = text.parse::<u64>()
    {
        return Ok(PyInt::new(py, value).into_any());
    }
    py_int(py, &text)
}

/// Returns the sort key that compares embedded numbers numerically, then the text.
///
/// >>> from e8s._core.core import natural_key
/// >>> natural_key("T2") < natural_key("T10")
/// True
#[pyfunction]
fn natural_key<'py>(text: &Bound<'py, PyString>) -> PyResult<Bound<'py, PyTuple>> {
    let py = text.py();
    let bytes = text_bytes(text)?;
    let chunks = guard("natural_key", || Ok(natural::natural_chunks(&bytes)))?;
    let keys = chunks
        .iter()
        .map(|chunk| {
            let (kind, length, run) = match *chunk {
                Chunk::Number(digits) => (NUMBER_KIND, digits.len(), digits),
                Chunk::Text(run) => (TEXT_KIND, 0, run),
            };
            PyTuple::new(
                py,
                [
                    kind.into_pyobject(py)?.into_any(),
                    length.into_pyobject(py)?.into_any(),
                    text_from_bytes(py, run)?.into_any(),
                ],
            )
        })
        .collect::<PyResult<Vec<_>>>()?;
    PyTuple::new(
        py,
        [PyTuple::new(py, keys)?.into_any(), text.clone().into_any()],
    )
}

/// Sorts IDs with embedded numbers compared numerically; ties fall back to the text.
///
/// >>> from e8s._core.core import natural_sort
/// >>> natural_sort(["T10", "T2"])
/// ['T2', 'T10']
#[pyfunction]
fn natural_sort<'py>(ids: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyList>> {
    let texts = strings(ids)?;
    let bytes = all_bytes(&texts)?;
    let views: Vec<&[u8]> = bytes.iter().map(AsRef::as_ref).collect();
    let order = guard("natural_sort", || Ok(natural::natural_order(&views)))?;
    PyList::new(ids.py(), order.into_iter().map(|index| &texts[index]))
}

/// Splits an ID into its prefix and trailing ASCII number, in time linear in its length.
///
/// Args:
/// text: The ID to split.
///
/// Returns:
/// parts: The text before the trailing digits and their value, or `(text, None)` without
/// trailing digits.
///
/// >>> from e8s._core.core import split_id
/// >>> split_id("flag-12"), split_id("lead")
/// (('flag-', 12), ('lead', None))
#[pyfunction]
fn split_id<'py>(
    text: &Bound<'py, PyAny>,
) -> PyResult<(Bound<'py, PyAny>, Option<Bound<'py, PyAny>>)> {
    let py = text.py();
    let string = text_or_error(text, "rstrip", Some(ASCII_DIGITS))?;
    let bytes = text_bytes(&string)?;
    let parts = run(py, "split_id", |host| ranges::split_id(host, &bytes))?;
    let Some(digits) = parts.digits else {
        return Ok((text.clone(), None));
    };
    Ok((
        text_from_bytes(py, parts.prefix)?.into_any(),
        Some(number(py, digits)?),
    ))
}

/// Expands an ID range such as `T1-T8` or `P1x1-P1x3`; a single ID returns itself.
///
/// The range splits at the `-` whose two sides each end in digits and share a prefix, so IDs
/// with an inner `-` such as `flag-1-flag-3` expand too. Both endpoints are spelled alike:
/// padded to one width (`T01-T10`) or both unpadded (`T1-T10`).
///
/// Args:
/// text: One ID or one range of IDs.
///
/// Returns:
/// ids: The IDs from the first endpoint to the last, in order.
///
/// >>> from e8s._core.core import parse_range
/// >>> parse_range("T1-T3")
/// ['T1', 'T2', 'T3']
/// >>> parse_range("T08-T10")
/// ['T08', 'T09', 'T10']
#[pyfunction]
fn parse_range<'py>(text: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyList>> {
    let py = text.py();
    let string = text_or_error(text, "strip", None)?;
    let bytes = text_bytes(&string)?;
    let ids = run(py, "parse_range", |host| ranges::parse_range(host, &bytes))?;
    let list = PyList::empty(py);
    ids.for_each_id(|id| list.append(text_from_bytes(py, id)?))?;
    Ok(list)
}

/// Compacts IDs into comma-separated runs that `parse_range` reads back, run by run.
///
/// Args:
/// ids: IDs to compact; duplicates collapse and the result is in natural order.
///
/// Returns:
/// text: Runs such as `T1-T3`; a run continues only while each ID is the next number in the
/// spelling of the run's first ID.
///
/// >>> from e8s._core.core import format_ranges
/// >>> format_ranges(["T1", "T2", "T3", "T7"])
/// 'T1-T3, T7'
#[pyfunction]
fn format_ranges<'py>(ids: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyString>> {
    let py = ids.py();
    let texts = strings(ids)?;
    let bytes = all_bytes(&texts)?;
    let views: Vec<&[u8]> = bytes.iter().map(AsRef::as_ref).collect();
    let text = run(py, "format_ranges", |host| {
        ranges::format_ranges(host, &views)
    })?;
    text_from_bytes(py, &text)
}

/// Adds the functions of this module to `module`.
///
/// # Errors
///
/// Returns the error of adding any function.
pub fn add_functions(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(natural_key, module)?)?;
    module.add_function(wrap_pyfunction!(natural_sort, module)?)?;
    module.add_function(wrap_pyfunction!(split_id, module)?)?;
    module.add_function(wrap_pyfunction!(parse_range, module)?)?;
    module.add_function(wrap_pyfunction!(format_ranges, module)?)
}

//! identifiers --- binds the ID checks, references and transition IDs
//!
//! Contains:
//!     check_id(): validates one ID and returns its NFC-normalized text
//!     check_sheet_id(): validates one sheet item or sheet reference ID the same way
//!     is_device_name(): tells whether text is a Windows device name
//!     find_case_clashes(): lists pairs of IDs that differ only by case
//!     parse_ref(): reads `kind:id@version` into its parts
//!     format_ref(): writes a reference from its parts
//!     split_tid(): reads `12→13` or `12->13` into its two set IDs
//!     format_tid(): writes a transition ID as `12→13`
//!     add_functions(): adds the functions above to a module

use e8s_core::base::ids::{self, IdRule};
use e8s_core::base::refs;
use e8s_core::base::text::StdText;
use pyo3::intern;
use pyo3::prelude::*;
use pyo3::pybacked::PyBackedStr;
use pyo3::types::PyString;

use super::host::{read_text, repr_of, run};
use crate::errors::guard;

const TID_PARTS: usize = 2;

/// Checks text under one rule and returns the stored form: the same object when it is ASCII.
fn check<'py>(
    name: &'static str,
    rule: IdRule,
    text: &Bound<'py, PyString>,
    kind: &str,
) -> PyResult<Bound<'py, PyString>> {
    let raw = read_text(text);
    if raw.is_ascii() {
        guard(name, || ids::check_normalized(&StdText, rule, &raw, kind))?;
        return Ok(text.clone());
    }
    let normalized = run(text.py(), name, |host| {
        ids::check_text(host, rule, &raw, kind)
    })?;
    Ok(PyString::new(text.py(), &normalized))
}

/// Validates one ID against the ID pattern and returns its NFC-normalized text.
///
/// Reserved names are rejected, and so is a Windows device name followed by an extension
/// (`con.5`), which Windows opens as the device.
///
/// Args:
/// text: Candidate ID as typed or stored.
/// kind: What the ID names (`spot`, `set`, `form`), used in the message and context.
///
/// Returns:
/// normalized_id: The ID after NFC normalization. Store this, not `text`: NFC can turn
/// non-ASCII input into a valid ID (the Kelvin sign becomes `K`).
///
/// >>> from e8s._core.core import check_id
/// >>> check_id("12A", kind="set")
/// '12A'
#[pyfunction]
#[pyo3(signature = (text, *, kind))]
fn check_id<'py>(text: &Bound<'py, PyString>, kind: &str) -> PyResult<Bound<'py, PyString>> {
    check("check_id", IdRule::Id, text, kind)
}

/// Validates one sheet item or sheet reference ID and returns its NFC-normalized text.
///
/// The rule is check_id's, except that `-` may appear anywhere but the first or last character:
/// sheet items and references never enter the selection grammar or a transition folder name.
///
/// Args:
/// text: Candidate ID as typed or stored.
/// kind: What the ID names (`guide`, `reference`), used in the message.
///
/// Returns:
/// normalized_id: The ID after NFC normalization; store this, not `text`.
///
/// >>> from e8s._core.core import check_sheet_id
/// >>> check_sheet_id("yard-left-10", kind="guide")
/// 'yard-left-10'
#[pyfunction]
#[pyo3(signature = (text, *, kind))]
fn check_sheet_id<'py>(text: &Bound<'py, PyString>, kind: &str) -> PyResult<Bound<'py, PyString>> {
    check("check_sheet_id", IdRule::Sheet, text, kind)
}

/// Tells whether text is a Windows device name, alone or before a dot, ignoring case.
///
/// Windows opens `con`, and `con.txt` too, as the console device instead of a file or folder.
///
/// Args:
/// text: Candidate name, such as an ID that becomes a path segment.
///
/// Returns:
/// is_device: True for `con`, `NUL` or `aux.1`; False for `cons`, `com10` or `x.con`.
///
/// >>> from e8s._core.core import is_device_name
/// >>> is_device_name("COM1.txt"), is_device_name("com10")
/// (True, False)
#[pyfunction]
fn is_device_name(text: &Bound<'_, PyString>) -> PyResult<bool> {
    let raw = read_text(text);
    if raw.is_ascii() {
        return guard("is_device_name", || ids::is_device_name(&StdText, &raw));
    }
    let folded = text.call_method0(intern!(text.py(), "casefold"))?;
    let folded = read_text(folded.cast::<PyString>()?).into_owned();
    guard("is_device_name", || Ok(ids::is_folded_device(&folded)))
}

/// Lists pairs of distinct IDs that are equal once case is folded.
///
/// Args:
/// ids: IDs of one namespace, such as the spots of a cast.
///
/// Returns:
/// clashes: Sorted pairs `(a, b)` with `a < b` and `a.casefold() == b.casefold()`.
///
/// >>> from e8s._core.core import find_case_clashes
/// >>> find_case_clashes(["T1", "t1", "B2"])
/// [('T1', 't1')]
#[pyfunction(name = "find_case_clashes")]
fn find_case_clashes_py(ids: &Bound<'_, PyAny>) -> PyResult<Vec<(String, String)>> {
    let items = ids
        .try_iter()?
        .map(|item| item?.extract::<PyBackedStr>())
        .collect::<PyResult<Vec<_>>>()?;
    run(ids.py(), "find_case_clashes", |host| {
        ids::find_case_clashes(host, items.iter().map(|item| &**item))
    })
}

/// Reads a reference such as `sheet:hs-field@2`.
///
/// The id must pass check_sheet_id and the version its own rule (letters, digits, `.`, `-` and
/// `+`, so `1.0.0+build.5` reads), so `..`, `/` and spaces never reach a path.
///
/// Args:
/// text: Reference text in `kind:id@version` form; the version is optional.
///
/// Returns:
/// parts: Kind, id and version (None when absent), the id and version NFC-normalized.
///
/// >>> from e8s._core.core import parse_ref
/// >>> parse_ref("sheet:gym@1")
/// ('sheet', 'gym', '1')
#[pyfunction]
fn parse_ref(text: &Bound<'_, PyString>) -> PyResult<(String, String, Option<String>)> {
    let raw = read_text(text);
    let parts = run(text.py(), "parse_ref", |host| refs::parse_ref(host, &raw))?;
    Ok((parts.kind, parts.id, parts.version))
}

/// Writes a reference from its parts.
///
/// >>> from e8s._core.core import format_ref
/// >>> format_ref("sheet", "gym", "2")
/// 'sheet:gym@2'
#[pyfunction]
#[pyo3(signature = (kind, ref_id, version = None))]
fn format_ref(py: Python<'_>, kind: &str, ref_id: &str, version: Option<&str>) -> PyResult<String> {
    run(py, "format_ref", |host| {
        refs::format_ref(host, kind, ref_id, version)
    })
}

/// Reads a transition ID written with `→` or `->` into its two set IDs; a third part is refused.
///
/// Only the shape is checked: stored pins and paths name sets that may predate the ID rule
/// (a v1 set `con`).
///
/// >>> from e8s._core.core import split_tid
/// >>> split_tid("12->13")
/// ('12', '13')
#[pyfunction]
fn split_tid(text: &Bound<'_, PyString>) -> PyResult<(String, String)> {
    let raw = read_text(text);
    run(text.py(), "split_tid", |host| refs::split_tid(host, &raw))
}

/// Writes a transition ID as `a→b`, refusing a tuple that is not a pair.
///
/// >>> from e8s._core.core import format_tid
/// >>> format_tid(("12", "13"))
/// '12→13'
#[pyfunction]
fn format_tid(tid: &Bound<'_, PyAny>) -> PyResult<String> {
    let parts = tid.len()?;
    if parts != TID_PARTS {
        let shown = repr_of(tid);
        return guard("format_tid", || {
            Err(refs::bad_tid_pair(&shown)
                .with_context("parts", i64::try_from(parts).unwrap_or(-1)))
        });
    }
    let (from_set, to_set) = (tid.get_item(0)?.str()?, tid.get_item(1)?.str()?);
    Ok(refs::format_tid(
        &from_set.to_string_lossy(),
        &to_set.to_string_lossy(),
    ))
}

/// Adds the functions of this module to `module`.
///
/// # Errors
///
/// Returns the error of adding any function.
pub fn add_functions(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(check_id, module)?)?;
    module.add_function(wrap_pyfunction!(check_sheet_id, module)?)?;
    module.add_function(wrap_pyfunction!(is_device_name, module)?)?;
    module.add_function(wrap_pyfunction!(find_case_clashes_py, module)?)?;
    module.add_function(wrap_pyfunction!(parse_ref, module)?)?;
    module.add_function(wrap_pyfunction!(format_ref, module)?)?;
    module.add_function(wrap_pyfunction!(split_tid, module)?)?;
    module.add_function(wrap_pyfunction!(format_tid, module)?)
}

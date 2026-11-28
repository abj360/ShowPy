//! strict --- binds the strict JSON reader, its nesting count and the strict copy of a value
//!
//! Contains:
//!     json_nesting(): measures how deep arrays and objects nest in JSON bytes
//!     require_nesting(): refuses JSON bytes nested deeper than MAX_NESTING levels
//!     parse_strict_json(): parses UTF-8 JSON bytes strictly
//!     strict_json_copy(): copies an already parsed JSON value through the strict parser
//!     add_functions(): adds the functions above and the reader's constants to a module

use std::borrow::Cow;

use e8s_core::base::canonical::{
    CONTEXT_TEXT_CHARS, MAP_TAG, MAX_INT_DIGITS, MAX_NESTING, TAG_PREFIX,
};
use e8s_core::base::strict_json::{
    self as strict, Invalid, ParseFailure, STRICT_HINT, SURROGATE_HINT, UTF8_BOM,
};
use pyo3::exceptions::{PyRecursionError, PyTypeError, PyUnicodeEncodeError, PyValueError};
use pyo3::intern;
use pyo3::prelude::*;
use pyo3::sync::PyOnceLock;
use pyo3::types::{PyByteArray, PyBytes, PyDict, PyString};

use super::canonical::to_python;
use super::host::run;
use super::walk::python_error;
use crate::errors::{guard, to_py_err};

static JSON_LOADS: PyOnceLock<Py<PyAny>> = PyOnceLock::new();
static JSON_DUMPS: PyOnceLock<Py<PyAny>> = PyOnceLock::new();
static INT_DIGIT_LIMIT: PyOnceLock<Py<PyAny>> = PyOnceLock::new();
/// U+D800 in generalized UTF-8, which Python decodes with `surrogatepass`.
const LONE_SURROGATE_UTF8: &[u8] = b"\xed\xa0\x80";

/// Reads JSON bytes from `bytes`, or from a `bytearray` as v0.4.0's byte methods allowed.
fn raw_bytes<'a>(raw: &'a Bound<'_, PyAny>) -> PyResult<Cow<'a, [u8]>> {
    if let Ok(bytes) = raw.cast::<PyBytes>() {
        return Ok(Cow::Borrowed(bytes.as_bytes()));
    }
    if let Ok(array) = raw.cast::<PyByteArray>() {
        return Ok(Cow::Owned(array.to_vec()));
    }
    Err(PyTypeError::new_err(format!(
        "JSON must be bytes or bytearray, not {}",
        raw.get_type().name()?
    )))
}

/// Measures how deep arrays and objects nest in JSON bytes, counting brackets outside strings.
///
/// Escaped backslashes and quotes are dropped first, so every quote left opens or closes a
/// string; brackets between a quote pair are skipped. The scan never recurses, so no
/// interpreter or stack limit decides the result. For text that is not JSON the count is only
/// an estimate; the parser refuses such text anyway.
///
/// Args:
/// raw: JSON bytes.
///
/// Returns:
/// depth: The most arrays and objects open at once; 0 for a bare scalar.
///
/// >>> from e8s._core.core import json_nesting
/// >>> json_nesting(b'{"a": [1, "]]"], "b": {}}')
/// 2
#[pyfunction]
fn json_nesting(raw: &Bound<'_, PyAny>) -> PyResult<usize> {
    let bytes = raw_bytes(raw)?;
    guard("json_nesting", || Ok(strict::json_nesting(&bytes)))
}

/// Refuses JSON bytes whose arrays and objects nest deeper than MAX_NESTING levels.
///
/// The check is explicit so the cap holds whatever recursion a JSON parser allows; the
/// canonical writer refuses the same depth.
///
/// Args:
/// raw: JSON bytes.
/// document: Path or name of the document, used in errors.
///
/// >>> from e8s._core.core import require_nesting
/// >>> from e8s.core.errors import SchemaError
/// >>> require_nesting(b"[[1]]", document="d.json")
/// >>> try:
/// ...     require_nesting(b"[" * 101 + b"]" * 101, document="d.json")
/// ... except SchemaError as exc:
/// ...     print(exc.context["max_nesting"])
/// 100
#[pyfunction]
#[pyo3(signature = (raw, *, document))]
fn require_nesting(raw: &Bound<'_, PyAny>, document: &Bound<'_, PyString>) -> PyResult<()> {
    let bytes = raw_bytes(raw)?;
    let name = document.to_string_lossy();
    guard("require_nesting", || strict::require_nesting(&bytes, &name))
}

/// Parses UTF-8 JSON bytes, refusing what standard JSON forbids or leaves ambiguous.
///
/// Args:
/// raw: Document bytes.
/// document: Path or name of the document, used in errors.
///
/// Returns:
/// value: The parsed value. Invalid JSON, nesting past MAX_NESTING or an int past the
/// interpreter's digit limit raises SchemaError; a NaN or infinity token, a repeated key, a
/// leading byte-order mark or a lone surrogate raises SchemaError code STRICT_JSON.
///
/// >>> from e8s._core.core import parse_strict_json
/// >>> from e8s.core.errors import SchemaError
/// >>> parse_strict_json(b'{"a": [1, 2.5]}', document="/show/show.json")
/// {'a': [1, 2.5]}
/// >>> try:
/// ...     parse_strict_json(b'{"a": NaN}', document="/show/show.json")
/// ... except SchemaError as exc:
/// ...     print(exc.code, exc.context["token"])
/// STRICT_JSON NaN
#[pyfunction]
#[pyo3(signature = (raw, *, document))]
fn parse_strict_json<'py>(
    raw: &Bound<'py, PyAny>,
    document: &Bound<'py, PyString>,
) -> PyResult<Bound<'py, PyAny>> {
    let bytes = raw_bytes(raw)?;
    parsed(raw.py(), &bytes, document)
}

fn parsed<'py>(
    py: Python<'py>,
    bytes: &[u8],
    document: &Bound<'py, PyString>,
) -> PyResult<Bound<'py, PyAny>> {
    let name = document.to_string_lossy();
    let limit = int_digit_limit(py)?;
    let outcome = run(py, "parse_strict_json", |host| {
        Ok(strict::parse_strict_json(bytes, &name, limit, host))
    })?;
    match outcome {
        Ok(value) => to_python(py, &value),
        Err(ParseFailure::Refused(error)) => Err(to_py_err(py, &error)),
        Err(ParseFailure::Invalid(invalid)) => Err(invalid_json(py, bytes, &name, invalid)),
        Err(ParseFailure::RepeatedKey(exact)) => Err(repeated_key(py, &exact, document)),
        Err(ParseFailure::LoneSurrogate) => Err(lone_surrogate(py, &name, None)),
    }
}

/// Reads the interpreter's limit on the digits of an int it converts from text; 0 for none.
fn int_digit_limit(py: Python<'_>) -> PyResult<usize> {
    INT_DIGIT_LIMIT
        .import(py, "sys", "get_int_max_str_digits")?
        .call0()?
        .extract()
}

/// Builds the error for bytes that are not valid JSON, in the words Python's own decoder and
/// JSON reader use for the same bytes, with their exception as the cause.
fn invalid_json(py: Python<'_>, bytes: &[u8], document: &str, invalid: Invalid) -> PyErr {
    let reason = PyBytes::new(py, bytes)
        .call_method1(intern!(py, "decode"), ("utf-8",))
        .and_then(|text| JSON_LOADS.import(py, "json", "loads")?.call1((text,)))
        .err()
        .filter(|failure| {
            failure.is_instance_of::<PyValueError>(py)
                || failure.is_instance_of::<PyRecursionError>(py)
        });
    let detail = reason
        .as_ref()
        .and_then(|failure| failure.value(py).str().ok())
        .map_or_else(
            || invalid.describe(),
            |text| text.to_string_lossy().into_owned(),
        );
    let error = to_py_err(py, &strict::invalid_json(document, &detail));
    error.set_cause(py, reason);
    error
}

/// Builds the STRICT_JSON error for a lone surrogate, caused by the UnicodeEncodeError of
/// writing it as UTF-8: the one given, or one raised for a lone surrogate here.
fn lone_surrogate(py: Python<'_>, document: &str, cause: Option<PyErr>) -> PyErr {
    let cause = cause.or_else(|| {
        PyBytes::new(py, LONE_SURROGATE_UTF8)
            .call_method1(intern!(py, "decode"), ("utf-8", "surrogatepass"))
            .and_then(|text| text.call_method1(intern!(py, "encode"), ("utf-8",)))
            .err()
    });
    let error = to_py_err(py, &strict::lone_surrogate_error(document));
    error.set_cause(py, cause);
    error
}

/// Builds the STRICT_JSON error for a repeated key that holds a lone surrogate, showing it as
/// Python's `repr` does.
fn repeated_key(py: Python<'_>, exact: &[u8], document: &Bound<'_, PyString>) -> PyErr {
    let built = (|| -> PyResult<PyErr> {
        let key = PyBytes::new(py, exact).call_method1("decode", ("utf-8", "surrogatepass"))?;
        let problem = format!("repeated key {}", key.repr()?.to_string_lossy());
        let context = PyDict::new(py);
        context.set_item("path", document)?;
        context.set_item("problem", &problem)?;
        context.set_item("key", &key)?;
        let message = format!(
            "{} is not strict JSON: {problem}",
            document.to_string_lossy()
        );
        Ok(python_error(
            py,
            "SchemaError",
            &message,
            STRICT_HINT,
            &context,
            Some("STRICT_JSON"),
        ))
    })();
    built.unwrap_or_else(|failure| failure)
}

/// Copies a parsed JSON value through the strict parser, so it obeys the same rules as bytes.
///
/// Args:
/// value: A value such as a dict a caller parsed itself.
/// document: Name of the document, used in errors.
///
/// Returns:
/// copy: A fresh JSON value equal to `value`. A value that is not JSON, holds NaN or infinity,
/// a lone surrogate or two keys that turn into one string raises SchemaError.
///
/// >>> from e8s._core.core import strict_json_copy
/// >>> strict_json_copy({"a": (1, 2)}, document="import")
/// {'a': [1, 2]}
#[pyfunction]
#[pyo3(signature = (value, *, document))]
fn strict_json_copy<'py>(
    value: &Bound<'py, PyAny>,
    document: &Bound<'py, PyString>,
) -> PyResult<Bound<'py, PyAny>> {
    let py = value.py();
    let name = document.to_string_lossy();
    let kwargs = PyDict::new(py);
    kwargs.set_item(intern!(py, "allow_nan"), false)?;
    kwargs.set_item(intern!(py, "ensure_ascii"), false)?;
    let dumped = JSON_DUMPS
        .import(py, "json", "dumps")?
        .call((value,), Some(&kwargs));
    let text = match dumped {
        Ok(text) => text,
        Err(failure) => return Err(copy_refused(py, &name, failure)),
    };
    let text = text.cast_into::<PyString>()?;
    match text.to_str() {
        Ok(readable) => parsed(py, readable.as_bytes(), document),
        Err(failure) => Err(lone_surrogate(py, &name, Some(failure))),
    }
}

/// Turns the TypeError, ValueError or RecursionError of `json.dumps` into a STRICT_JSON
/// error; any other exception passes through.
fn copy_refused(py: Python<'_>, document: &str, failure: PyErr) -> PyErr {
    if failure.is_instance_of::<PyUnicodeEncodeError>(py) {
        return lone_surrogate(py, document, Some(failure));
    }
    let refused = failure.is_instance_of::<PyTypeError>(py)
        || failure.is_instance_of::<PyValueError>(py)
        || failure.is_instance_of::<PyRecursionError>(py);
    if !refused {
        return failure;
    }
    let value = failure.value(py);
    let detail = value.str().map_or_else(
        |_| String::new(),
        |text| text.to_string_lossy().into_owned(),
    );
    let kind = value
        .get_type()
        .name()
        .map_or_else(|_| String::new(), |name| name.to_string());
    let error = to_py_err(py, &strict::copy_refused(document, &detail, &kind));
    error.set_cause(py, Some(failure));
    error
}

/// Adds the functions of this module and the reader's and canonical form's constants to
/// `module`.
///
/// # Errors
///
/// Returns the error of adding any of them.
pub fn add_functions(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(json_nesting, module)?)?;
    module.add_function(wrap_pyfunction!(require_nesting, module)?)?;
    module.add_function(wrap_pyfunction!(parse_strict_json, module)?)?;
    module.add_function(wrap_pyfunction!(strict_json_copy, module)?)?;
    module.add("UTF8_BOM", PyBytes::new(module.py(), UTF8_BOM))?;
    module.add("STRICT_HINT", STRICT_HINT)?;
    module.add("SURROGATE_HINT", SURROGATE_HINT)?;
    module.add("MAX_NESTING", MAX_NESTING)?;
    module.add("MAX_INT_DIGITS", MAX_INT_DIGITS)?;
    module.add("TAG_PREFIX", TAG_PREFIX)?;
    module.add("MAP_TAG", MAP_TAG)?;
    module.add("CONTEXT_TEXT_CHARS", CONTEXT_TEXT_CHARS)
}

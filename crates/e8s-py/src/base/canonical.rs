//! canonical --- binds canonical values, canonical JSON bytes and content hashes
//!
//! Contains:
//!     to_json(): walks a Python value into the canonical JSON model in one pass
//!     to_python(): builds the plain Python value of a JSON model value
//!     canonical_value(): normalizes a supported value into the JSON data model
//!     jcs(): serializes a value as RFC 8785 (JCS) canonical JSON bytes
//!     content_hash(): hashes bytes, text or canonical JSON under a domain tag
//!     asset_uri(): names bytes by the `asset:sha256:<hex>` URI an asset store returns
//!     add_functions(): adds the functions above to a module

use std::collections::HashMap;
use std::panic::AssertUnwindSafe;

use e8s_core::base::canonical::{self as canon, CONTEXT_TEXT_CHARS, Json, MAX_INT_DIGITS};
use e8s_core::base::hashing::{self, DEFAULT_DOMAIN, HashKind};
use pyo3::exceptions::PyTypeError;
use pyo3::prelude::*;
use pyo3::sync::PyOnceLock;
use pyo3::types::{PyBool, PyBytes, PyDict, PyFloat, PyInt, PyList, PySlice, PyString};

use super::host::run;
use super::walk::{Walker, checked_text, python_error};
use crate::errors::guard;

static PYTHON_HASHLIB_SHA256: PyOnceLock<Py<PyAny>> = PyOnceLock::new();
const DOMAIN_PATTERN: &str = "[a-z0-9_.]+";
/// The payload size from which hashing lets other threads run, hashlib's own threshold.
const GIL_FREE_BYTES: usize = 2048;

/// Builds the plain Python value of a JSON model value.
///
/// Equal object keys share one Python string, as CPython's JSON reader shares them, so each
/// key's hash is computed once.
///
/// # Errors
///
/// Returns the error of creating a Python object.
pub fn to_python<'py>(py: Python<'py>, value: &Json) -> PyResult<Bound<'py, PyAny>> {
    PythonBuilder {
        py,
        keys: HashMap::new(),
    }
    .build(value)
}

/// Builds Python values, sharing one string per distinct object key.
struct PythonBuilder<'py, 'a> {
    py: Python<'py>,
    keys: HashMap<&'a str, Bound<'py, PyString>>,
}

impl<'py, 'a> PythonBuilder<'py, 'a> {
    fn build(&mut self, value: &'a Json) -> PyResult<Bound<'py, PyAny>> {
        let py = self.py;
        Ok(match value {
            Json::Null => py.None().into_bound(py),
            Json::Bool(flag) => PyBool::new(py, *flag).to_owned().into_any(),
            Json::Int(number) => number.into_pyobject(py)?.into_any(),
            Json::BigInt(text) => py.get_type::<PyInt>().call1((text,))?,
            Json::Float(number) => PyFloat::new(py, *number).into_any(),
            Json::Text(text) => PyString::new(py, text).into_any(),
            Json::Array(items) => {
                let built = items
                    .iter()
                    .map(|item| self.build(item))
                    .collect::<PyResult<Vec<_>>>()?;
                PyList::new(py, built)?.into_any()
            }
            Json::Object(members) => {
                let dict = PyDict::new(py);
                for (key, item) in members {
                    let built = self.build(item)?;
                    let shared = self
                        .keys
                        .entry(key.as_str())
                        .or_insert_with(|| PyString::new(py, key));
                    dict.set_item(&*shared, built)?;
                }
                dict.into_any()
            }
        })
    }
}

/// Normalizes a supported value into the JSON data model.
///
/// Models dump in JSON mode, tuples become lists, sets become lists sorted by canonical bytes,
/// `-0.0` becomes `0.0`, and a mapping with any non-string key becomes the tagged form
/// `{"$showpy:map": [[key, value], ...]}` sorted by the canonical bytes of each pair.
///
/// A set held by a model field is dumped by pydantic in iteration order, before this function
/// sees it, so it is not sorted; that order can change with `PYTHONHASHSEED` until stored
/// models hold tuples instead. Containers nest at most 100 levels and ints stay under 10**640.
///
/// Args:
/// obj: Value to normalize.
///
/// Returns:
/// value: The canonical value, built only from plain `dict`, `list`, `str`, `int`, `float`,
/// `bool` and `None`.
///
/// >>> from e8s._core.core import canonical_value
/// >>> canonical_value({2: "b", "a": frozenset({3, 1})})
/// {'$showpy:map': [['a', [1, 3]], [2, 'b']]}
#[pyfunction]
fn canonical_value<'py>(obj: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    let value = Walker::new(obj.py())?.walk(obj, 0)?;
    to_python(obj.py(), &value)
}

/// Serializes a value as RFC 8785 canonical JSON after normalizing it with canonical_value.
///
/// Args:
/// obj: Value to serialize.
///
/// Returns:
/// canonical_bytes: UTF-8 JSON with no whitespace, keys sorted by UTF-16 code units and
/// numbers in ECMAScript shortest round-trip form.
///
/// >>> from e8s._core.core import jcs
/// >>> jcs({"b": 1.0, "a": [True, None]})
/// b'{"a":[true,null],"b":1}'
#[pyfunction]
fn jcs<'py>(obj: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyBytes>> {
    let value = Walker::new(obj.py())?.walk(obj, 0)?;
    let bytes = guard("jcs", || Ok(canon::encoded(&value)))?;
    Ok(PyBytes::new(obj.py(), &bytes))
}

/// Hashes a value as SHA-256 over domain-tagged canonical JSON.
///
/// Bytes and text hash as themselves under their own kind tag, so the string `'{"a":1}'` and
/// the object `{"a": 1}` never collide. Everything else hashes as RFC 8785 JSON.
///
/// Args:
/// obj: Bytes, text or any value `canonical_value` accepts.
/// domain: Name of what the hash identifies, so equal content in two domains differs;
/// only lowercase ASCII letters, digits, `_` and `.`.
///
/// Returns:
/// digest: 64 lowercase hex characters.
///
/// >>> from e8s._core.core import content_hash
/// >>> content_hash({"a": 1}) == content_hash({"a": 1.0})
/// True
/// >>> content_hash({"a": 1}) == content_hash({"a": 1}, domain="showpy.revision")
/// False
#[pyfunction]
#[pyo3(
    signature = (obj, *, domain = DomainArg::Default),
    text_signature = "(obj, *, domain='showpy.content')"
)]
fn content_hash(obj: &Bound<'_, PyAny>, domain: DomainArg<'_>) -> PyResult<String> {
    let domain = match domain {
        DomainArg::Given(given) => checked_domain(&given)?,
        DomainArg::Default => DEFAULT_DOMAIN.to_owned(),
    };
    let py = obj.py();
    if let Ok(bytes) = obj.cast::<PyBytes>() {
        return hashed(py, "content_hash", bytes.as_bytes(), |payload| {
            hashing::content_hash(&domain, HashKind::Bytes, payload)
        });
    }
    let (kind, payload) = if let Ok(text) = obj.cast::<PyString>() {
        (HashKind::Str, checked_text(text)?.into_bytes())
    } else {
        let value = Walker::new(obj.py())?.walk(obj, 0)?;
        (
            HashKind::Json,
            guard("content_hash", || Ok(canon::encoded(&value)))?,
        )
    };
    hashed(py, "content_hash", &payload, |payload| {
        hashing::content_hash(&domain, kind, payload)
    })
}

/// Hashes a payload under the error boundary, letting other Python threads run while a
/// payload of GIL_FREE_BYTES or more is hashed, as hashlib does.
fn hashed(
    py: Python<'_>,
    name: &'static str,
    payload: &[u8],
    hash: impl Fn(&[u8]) -> String + Sync,
) -> PyResult<String> {
    guard(
        name,
        AssertUnwindSafe(|| {
            Ok(if payload.len() < GIL_FREE_BYTES {
                hash(payload)
            } else {
                py.detach(|| hash(payload))
            })
        }),
    )
}

/// The `domain` argument of content_hash: absent, or whatever object the caller passed, even
/// None, which is then refused as v0.4.0 refused it.
enum DomainArg<'py> {
    Default,
    Given(Bound<'py, PyAny>),
}

impl<'a, 'py> FromPyObject<'a, 'py> for DomainArg<'py> {
    type Error = PyErr;

    fn extract(obj: Borrowed<'a, 'py, PyAny>) -> PyResult<Self> {
        Ok(Self::Given(obj.to_owned()))
    }
}

/// Reads a hash domain and refuses one that is not lowercase ASCII letters, digits, _ and .
///
/// A domain that is not text raises the TypeError Python's regular expressions raise for it.
fn checked_domain(domain: &Bound<'_, PyAny>) -> PyResult<String> {
    let py = domain.py();
    let Ok(text) = domain.cast::<PyString>() else {
        let pattern = py
            .import("re")?
            .call_method1("compile", (DOMAIN_PATTERN,))?;
        pattern.call_method1("fullmatch", (domain,))?;
        return Err(PyTypeError::new_err("a hash domain must be text"));
    };
    if let Ok(readable) = text.to_str() {
        run(py, "content_hash", |host| {
            hashing::check_domain(host, readable)
        })?;
        return Ok(readable.to_owned());
    }
    let cut = text.get_item(PySlice::new(py, 0, cut_chars(), 1))?;
    let refused = hashing::domain_refused(&cut.repr()?.to_string_lossy(), "");
    let context = PyDict::new(py);
    context.set_item("domain", &cut)?;
    Err(python_error(
        py,
        "SchemaError",
        refused.message(),
        refused.hint(),
        &context,
        None,
    ))
}

fn cut_chars() -> isize {
    isize::try_from(CONTEXT_TEXT_CHARS).unwrap_or(isize::MAX)
}

/// Names bytes by the `asset:sha256:<hex>` URI an asset store returns for them.
///
/// Args:
/// data: Exact bytes of the asset.
///
/// Returns:
/// uri: `asset:sha256:` followed by the SHA-256 hex digest of `data`.
///
/// >>> from e8s._core.core import asset_uri
/// >>> asset_uri(b"")
/// 'asset:sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'
#[pyfunction]
fn asset_uri(data: &Bound<'_, PyAny>) -> PyResult<String> {
    if let Ok(bytes) = data.cast::<PyBytes>() {
        return hashed(data.py(), "asset_uri", bytes.as_bytes(), hashing::asset_uri);
    }
    let py = data.py();
    let sha256 = PYTHON_HASHLIB_SHA256.import(py, "hashlib", "sha256")?;
    let digest = sha256.call1((data,))?.call_method0("hexdigest")?;
    Ok(format!(
        "{}{}",
        hashing::ASSET_PREFIX,
        digest.str()?.to_str()?
    ))
}

/// Adds the functions of this module to `module`.
///
/// # Errors
///
/// Returns the error of adding any function.
pub fn add_functions(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(canonical_value, module)?)?;
    module.add_function(wrap_pyfunction!(jcs, module)?)?;
    module.add_function(wrap_pyfunction!(content_hash, module)?)?;
    module.add_function(wrap_pyfunction!(asset_uri, module)?)?;
    let builtins = module.py().import("builtins")?;
    module.add(
        "INT_LIMIT",
        builtins.call_method1("pow", (10, MAX_INT_DIGITS))?,
    )?;
    module.add("HASH_PREFIX", hashing::HASH_PREFIX)?;
    module.add("ASSET_PREFIX", hashing::ASSET_PREFIX)
}

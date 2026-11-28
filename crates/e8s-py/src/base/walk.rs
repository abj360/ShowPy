//! walk --- walks Python values into the canonical JSON model, as canonical_value defines it
//!
//! Contains:
//!     Walker: holds the Python types the walk tells apart
//!     Walker.walk(): normalizes one value whose container nesting so far is `depth`
//!     checked_text(): reads text, refusing a lone surrogate with its index
//!     python_error(): builds an e8s exception whose context holds Python objects
//!     numpy_content(): dumps a numpy value held by a model field in full

use std::collections::HashMap;

use e8s_core::base::CoreError;
use e8s_core::base::canonical::{self as canon, CONTEXT_TEXT_CHARS, Json, MAX_NESTING, TAG_PREFIX};
use pyo3::intern;
use pyo3::prelude::*;
use pyo3::sync::PyOnceLock;
use pyo3::types::{
    PyBool, PyBytes, PyCFunction, PyDict, PyFloat, PyFrozenSet, PyInt, PyList, PySet, PySlice,
    PyString, PyTuple, PyType,
};

use crate::errors::{guard, loaded, to_py_err};

static BASE_MODEL: PyOnceLock<Py<PyType>> = PyOnceLock::new();
static SERIALIZATION_ERROR: PyOnceLock<Py<PyType>> = PyOnceLock::new();
static NDARRAY: PyOnceLock<Py<PyType>> = PyOnceLock::new();
static NUMPY_GENERIC: PyOnceLock<Py<PyType>> = PyOnceLock::new();
static MAPPING: PyOnceLock<Py<PyType>> = PyOnceLock::new();
static INT_LIMIT: PyOnceLock<Py<PyAny>> = PyOnceLock::new();
static FALLBACK: PyOnceLock<Py<PyCFunction>> = PyOnceLock::new();
const ERRORS_MODULE: &str = "e8s.core.errors";
const CODES_MODULE: &str = "e8s.core.error_codes";

/// Holds the Python types the walk tells apart, looked up once per process.
pub struct Walker<'py> {
    py: Python<'py>,
    base_model: Bound<'py, PyType>,
    ndarray: Bound<'py, PyType>,
    numpy_generic: Bound<'py, PyType>,
    mapping: Bound<'py, PyType>,
}

impl<'py> Walker<'py> {
    /// Looks up the pydantic, numpy and `collections.abc` types the walk tells apart.
    ///
    /// # Errors
    ///
    /// Returns the import error of any of them.
    pub fn new(py: Python<'py>) -> PyResult<Self> {
        Ok(Self {
            py,
            base_model: BASE_MODEL.import(py, "pydantic", "BaseModel")?.clone(),
            ndarray: NDARRAY.import(py, "numpy", "ndarray")?.clone(),
            numpy_generic: NUMPY_GENERIC.import(py, "numpy", "generic")?.clone(),
            mapping: MAPPING.import(py, "collections.abc", "Mapping")?.clone(),
        })
    }

    /// Normalizes one value whose container nesting so far is `depth`.
    ///
    /// The checks run in canonical_value's order: the nesting cap, scalars, models, numpy
    /// values, mappings, lists and tuples, sets, and last the refusal of any other type.
    ///
    /// # Errors
    ///
    /// Returns the SchemaError of the first value the canonical form cannot hold, or the
    /// Python error a model dump, an iteration or a conversion raised.
    pub fn walk(&self, obj: &Bound<'py, PyAny>, depth: usize) -> PyResult<Json> {
        if depth > MAX_NESTING {
            return Err(raise(canon::nesting_error()));
        }
        if obj.is_none() {
            return Ok(Json::Null);
        }
        if let Ok(flag) = obj.cast_exact::<PyBool>() {
            return Ok(Json::Bool(flag.is_true()));
        }
        if let Ok(text) = obj.cast::<PyString>() {
            return checked_text(text).map(Json::Text);
        }
        if obj.is_instance_of::<PyInt>() {
            return self.int(obj);
        }
        if let Ok(number) = obj.cast_exact::<PyFloat>() {
            return checked_float(number.value());
        }
        if obj.is_instance_of::<PyFloat>() {
            return checked_float(self.py.get_type::<PyFloat>().call1((obj,))?.extract()?);
        }
        if let Ok(dict) = obj.cast_exact::<PyDict>() {
            let items = dict.iter().collect();
            return self.mapping(items, depth + 1, true);
        }
        if obj.is_exact_instance_of::<PyList>() || obj.is_exact_instance_of::<PyTuple>() {
            return self.sequence(obj, depth + 1);
        }
        self.other(obj, depth)
    }

    /// Normalizes a value that is no exact builtin container, in canonical_value's order.
    fn other(&self, obj: &Bound<'py, PyAny>, depth: usize) -> PyResult<Json> {
        if obj.is_instance(&self.base_model)? {
            return self.walk(&self.dumped(obj)?, depth);
        }
        if obj.is_instance(&self.ndarray)? {
            return self.walk(&obj.call_method0(intern!(self.py, "tolist"))?, depth);
        }
        if obj.is_instance(&self.numpy_generic)? {
            return self.walk(&obj.call_method0(intern!(self.py, "item"))?, depth);
        }
        if obj.is_instance(&self.mapping)? {
            let items = obj
                .call_method0(intern!(self.py, "items"))?
                .try_iter()?
                .map(|pair| pair.and_then(|pair| pair.extract()))
                .collect::<PyResult<Vec<_>>>()?;
            return self.mapping(items, depth + 1, false);
        }
        if obj.is_instance_of::<PyList>() || obj.is_instance_of::<PyTuple>() {
            return self.sequence(obj, depth + 1);
        }
        if obj.is_instance_of::<PySet>() || obj.is_instance_of::<PyFrozenSet>() {
            let members = obj
                .try_iter()?
                .map(|member| member.and_then(|member| self.walk(&member, depth + 1)))
                .collect::<PyResult<Vec<_>>>()?;
            return Ok(Json::Array(canon::sorted_members(members)));
        }
        let kind = obj.get_type();
        Err(raise(canon::unsupported_type(
            &kind.name()?.to_string_lossy(),
            &kind.qualname()?.to_string_lossy(),
        )))
    }

    fn sequence(&self, obj: &Bound<'py, PyAny>, depth: usize) -> PyResult<Json> {
        let items = if let Ok(list) = obj.cast_exact::<PyList>() {
            list.iter().map(|item| self.walk(&item, depth)).collect()
        } else if let Ok(tuple) = obj.cast_exact::<PyTuple>() {
            tuple.iter().map(|item| self.walk(&item, depth)).collect()
        } else {
            obj.try_iter()?
                .map(|item| item.and_then(|item| self.walk(&item, depth)))
                .collect::<PyResult<Vec<_>>>()
        };
        items.map(Json::Array)
    }

    /// Reads an int, keeping it exact; an int of 640 or more digits is refused.
    fn int(&self, obj: &Bound<'py, PyAny>) -> PyResult<Json> {
        let exact;
        let number = if obj.is_exact_instance_of::<PyInt>() {
            obj
        } else {
            exact = self.py.get_type::<PyInt>().call1((obj,))?;
            &exact
        };
        if let Ok(small) = number.extract::<i64>() {
            return Ok(Json::Int(small));
        }
        let limit = INT_LIMIT.get_or_try_init(self.py, || {
            let builtins = self.py.import(intern!(self.py, "builtins"))?;
            builtins
                .call_method1(intern!(self.py, "pow"), (10, canon::MAX_INT_DIGITS))
                .map(Bound::unbind)
        })?;
        let size = number.call_method0(intern!(self.py, "__abs__"))?;
        if size.lt(limit.bind(self.py))? {
            return Ok(Json::BigInt(number.str()?.to_str()?.to_owned()));
        }
        let bits: i64 = number
            .call_method0(intern!(self.py, "bit_length"))?
            .extract()?;
        Err(raise(canon::int_too_long(bits)))
    }

    /// Normalizes a mapping's pairs to a JSON object, or to the tagged form if any key is not
    /// text; a key that starts with the reserved prefix is refused before any value is read.
    ///
    /// Keys that turn into equal text keep the first one's place and the last one's value, as
    /// a dict built from them does; `from_dict` says the pairs came from an exact dict.
    fn mapping(
        &self,
        items: Vec<(Bound<'py, PyAny>, Bound<'py, PyAny>)>,
        depth: usize,
        from_dict: bool,
    ) -> PyResult<Json> {
        let mut all_text = true;
        for (key, _) in &items {
            match key.cast::<PyString>() {
                Ok(text) if reserved(text)? => return Err(reserved_key(text)?),
                Ok(_) => {}
                Err(_) => all_text = false,
            }
        }
        if !all_text {
            let pairs = items
                .iter()
                .map(|(key, value)| Ok((self.walk(key, depth)?, self.walk(value, depth)?)))
                .collect::<PyResult<Vec<_>>>()?;
            return Ok(canon::tagged_map(pairs));
        }
        let mut members: Vec<(String, Json)> = Vec::with_capacity(items.len());
        let unique = from_dict
            && items
                .iter()
                .all(|(key, _)| key.is_exact_instance_of::<PyString>());
        let mut places: HashMap<String, usize> = HashMap::new();
        for (key, value) in &items {
            let text = checked_text(key.cast::<PyString>()?)?;
            let normalized = self.walk(value, depth)?;
            if unique {
                members.push((text, normalized));
                continue;
            }
            if let Some(&place) = places.get(&text) {
                if let Some(member) = members.get_mut(place) {
                    member.1 = normalized;
                }
                continue;
            }
            places.insert(text.clone(), members.len());
            members.push((text, normalized));
        }
        Ok(Json::Object(members))
    }

    /// Dumps a model in JSON mode, turning serializer failures and reference cycles into
    /// SchemaError.
    fn dumped(&self, model: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
        let fallback = FALLBACK.get_or_try_init(self.py, || {
            PyCFunction::new_closure(self.py, None, None, |args, _kwargs| {
                numpy_content(&args.get_item(0)?).map(Bound::unbind)
            })
            .map(Bound::unbind)
        })?;
        let kwargs = PyDict::new(self.py);
        kwargs.set_item(intern!(self.py, "mode"), intern!(self.py, "json"))?;
        kwargs.set_item(intern!(self.py, "fallback"), fallback.bind(self.py))?;
        let failure = match model.call_method(intern!(self.py, "model_dump"), (), Some(&kwargs)) {
            Ok(dumped) => return Ok(dumped),
            Err(failure) => failure,
        };
        let serialization =
            SERIALIZATION_ERROR.import(self.py, "pydantic_core", "PydanticSerializationError")?;
        let caught = failure.is_instance(self.py, serialization.as_any())
            || failure.is_instance_of::<pyo3::exceptions::PyValueError>(self.py);
        if !caught {
            return Err(failure);
        }
        let reason = loaded(self.py, ERRORS_MODULE)?
            .getattr(intern!(self.py, "bounded_repr"))?
            .call1((failure.value(self.py).str()?,))?;
        let name = model.get_type().name()?;
        let error = raise(canon::model_dump_failed(
            &name.to_string_lossy(),
            &reason.str()?.to_string_lossy(),
        ));
        error.set_cause(self.py, Some(failure));
        Err(error)
    }
}

fn raise(error: CoreError) -> PyErr {
    Python::attach(|py| to_py_err(py, &error))
}

fn checked_float(number: f64) -> PyResult<Json> {
    guard("canonical_value", || canon::checked_float(number)).map(Json::Float)
}

/// Tells whether a text key starts with the reserved prefix, through the key's own method.
fn reserved(key: &Bound<'_, PyString>) -> PyResult<bool> {
    if key.is_exact_instance_of::<PyString>() {
        return Ok(key.to_string_lossy().starts_with(TAG_PREFIX));
    }
    key.call_method1(intern!(key.py(), "startswith"), (TAG_PREFIX,))?
        .extract()
}

fn reserved_key(key: &Bound<'_, PyString>) -> PyResult<PyErr> {
    let cut = head(key)?;
    let shown = cut.repr()?.to_string_lossy().into_owned();
    let escaped = ascii(&cut)?;
    Ok(raise(canon::reserved_key(&shown, &escaped)))
}

fn head<'py>(text: &Bound<'py, PyString>) -> PyResult<Bound<'py, PyAny>> {
    let chars = isize::try_from(CONTEXT_TEXT_CHARS).unwrap_or(isize::MAX);
    text.get_item(PySlice::new(text.py(), 0, chars, 1))
}

fn ascii(text: &Bound<'_, PyAny>) -> PyResult<String> {
    let builtins = text.py().import(intern!(text.py(), "builtins"))?;
    Ok(builtins
        .call_method1(intern!(text.py(), "ascii"), (text,))?
        .str()?
        .to_string_lossy()
        .into_owned())
}

/// Reads text as plain UTF-8, refusing a lone surrogate, which UTF-8 cannot encode.
///
/// # Errors
///
/// Returns a SchemaError naming the index of the first lone surrogate.
pub fn checked_text(text: &Bound<'_, PyString>) -> PyResult<String> {
    if let Ok(readable) = text.to_str() {
        return Ok(readable.to_owned());
    }
    let py = text.py();
    let units = text.call_method1(intern!(py, "encode"), ("utf-32-be", "surrogatepass"))?;
    let units = units.cast::<PyBytes>()?.as_bytes();
    let position = units
        .as_chunks::<4>()
        .0
        .iter()
        .position(|[high, middle, unit, _]| {
            *high == 0 && *middle == 0 && (0xd8..0xe0).contains(unit)
        })
        .unwrap_or(0);
    let shown = ascii(&head(text)?)?;
    Err(raise(canon::lone_surrogate(
        i64::try_from(position).unwrap_or(i64::MAX),
        &shown,
    )))
}

/// Builds an e8s exception of the class `class_name` names whose context holds Python
/// objects, for text that holds a lone surrogate and so has no Rust form.
///
/// # Errors
///
/// Returns the error of building the exception instead when it cannot be built.
pub fn python_error(
    py: Python<'_>,
    class_name: &str,
    message: &str,
    hint: &str,
    context: &Bound<'_, PyDict>,
    code: Option<&str>,
) -> PyErr {
    let built = (|| -> PyResult<PyErr> {
        let class = loaded(py, ERRORS_MODULE)?.getattr(class_name)?;
        let kwargs = PyDict::new(py);
        kwargs.set_item(intern!(py, "hint"), hint)?;
        kwargs.set_item(intern!(py, "context"), context)?;
        if let Some(code) = code {
            let codes = loaded(py, CODES_MODULE)?.getattr(intern!(py, "ErrorCode"))?;
            kwargs.set_item(intern!(py, "code"), codes.getattr(code)?)?;
        }
        Ok(PyErr::from_value(class.call((message,), Some(&kwargs))?))
    })();
    built.unwrap_or_else(|failure| failure)
}

/// Dumps a numpy value held by a model field in full; any other unknown type is rejected.
///
/// # Errors
///
/// Returns a SchemaError naming the field value's type.
pub fn numpy_content<'py>(value: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    let py = value.py();
    if value.is_instance(NDARRAY.import(py, "numpy", "ndarray")?)? {
        return value.call_method0(intern!(py, "tolist"));
    }
    if value.is_instance(NUMPY_GENERIC.import(py, "numpy", "generic")?)? {
        return value.call_method0(intern!(py, "item"));
    }
    let kind = value.get_type();
    let error = canon::unsupported_field(
        &kind.name()?.to_string_lossy(),
        &kind.qualname()?.to_string_lossy(),
    );
    guard("canonical_value", || Err(error))
}

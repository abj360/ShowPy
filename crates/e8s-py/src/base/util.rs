//! util --- binds noise ranks, float comparison and count chunks
//!
//! Contains:
//!     noise_rank(): rounds a float that decides an order so 1-ulp noise ties
//!     approx_equal(): compares floats or arrays within absolute and relative tolerances
//!     chunk_counts(): splits counts into equal parts that sum exactly
//!     add_functions(): adds the functions above to a module

use e8s_core::base::caps::{self, MAX_POINTS};
use e8s_core::base::numbers::py_float_repr;
use e8s_core::base::util::{self, DEFAULT_ABS_TOL};
use e8s_core::base::{ContextValue, CoreError, CoreResult, ErrorCode, ErrorKind};
use numpy::{PyArrayDyn, PyArrayMethods, PyUntypedArrayMethods};
use pyo3::exceptions::{PyOverflowError, PyTypeError};
use pyo3::intern;
use pyo3::prelude::*;
use pyo3::sync::PyOnceLock;
use pyo3::types::{PyBool, PyDict, PyFloat, PyInt};

use super::host::repr_of;
use crate::errors::guard;

static AS_ARRAY: PyOnceLock<Py<PyAny>> = PyOnceLock::new();
/// Ints up to this size convert to a float exactly, so dividing the float divides the int.
const EXACT_INT_LIMIT: f64 = 9_007_199_254_740_992.0;

/// A keyword argument: absent, or whatever object the caller passed, even None.
pub enum Keyword<'py> {
    Default,
    Given(Bound<'py, PyAny>),
}

impl<'a, 'py> FromPyObject<'a, 'py> for Keyword<'py> {
    type Error = PyErr;

    fn extract(obj: Borrowed<'a, 'py, PyAny>) -> PyResult<Self> {
        Ok(Self::Given(obj.to_owned()))
    }
}

/// Rounds a float that decides an order to 9 decimals, so noise ties.
///
/// Rounds as builtin `round` does, never as `np.round`: numpy rounds by scaling, which can land
/// 1 ulp off the correctly rounded value and so reintroduce the noise.
///
/// >>> from e8s._core.core import noise_rank
/// >>> noise_rank(-0.5999999999999999) == noise_rank(-0.6)
/// True
#[pyfunction]
fn noise_rank(value: &Bound<'_, PyAny>) -> PyResult<f64> {
    let number = match value.cast::<PyFloat>() {
        Ok(float) => float.value(),
        Err(_) => value
            .py()
            .get_type::<PyFloat>()
            .call1((value,))?
            .extract()?,
    };
    guard("noise_rank", || Ok(util::noise_rank(number)))
}

/// Reads one tolerance: the default when absent, else the caller's object checked as
/// `not value >= 0` checks it.
fn tolerance(name: &'static str, given: &Keyword<'_>, default: f64) -> PyResult<f64> {
    let Keyword::Given(value) = given else {
        return Ok(default);
    };
    let (number, is_valid) = match value.cast::<PyFloat>() {
        Ok(float) => (float.value(), float.value() >= 0.0),
        Err(_) => {
            let is_valid = value.ge(0)?;
            (if is_valid { value.extract()? } else { f64::NAN }, is_valid)
        }
    };
    if !is_valid {
        let shown = repr_of(value);
        guard("approx_equal", || {
            util::require_tolerance(name, f64::NAN, &shown)
        })?;
    }
    Ok(number)
}

/// Reads a float or an int as one number, or None for anything numpy must convert.
fn scalar(value: &Bound<'_, PyAny>) -> PyResult<Option<f64>> {
    if let Ok(float) = value.cast::<PyFloat>() {
        return Ok(Some(float.value()));
    }
    if value.is_instance_of::<PyInt>() {
        return Ok(Some(value.extract()?));
    }
    Ok(None)
}

/// Converts any array-like value to a float64 array, as `np.asarray(value, dtype=float)` does.
fn float_array<'py>(value: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyArrayDyn<f64>>> {
    let py = value.py();
    let as_array = AS_ARRAY.import(py, "numpy", "asarray")?;
    let options = PyDict::new(py);
    options.set_item(intern!(py, "dtype"), py.get_type::<PyFloat>())?;
    Ok(as_array.call((value,), Some(&options))?.cast_into()?)
}

/// Compares numbers or arrays within `abs_tol` plus `rel_tol` of `b`; shapes must match.
///
/// Equal infinities are equal and NaN equals nothing, without any warning. A negative or NaN
/// tolerance raises SchemaError.
///
/// Args:
/// a: First number or array.
/// b: Second number or array.
/// abs_tol: Absolute tolerance; 1e-6 when omitted.
/// rel_tol: Tolerance relative to the magnitude of `b`.
///
/// Returns:
/// is_close: Whether every element of `a` is within tolerance of its element of `b`.
///
/// >>> from e8s._core.core import approx_equal
/// >>> approx_equal(0.1 + 0.2, 0.3)
/// True
#[pyfunction]
#[pyo3(
    signature = (a, b, *, abs_tol = Keyword::Default, rel_tol = Keyword::Default),
    text_signature = "(a, b, *, abs_tol=1e-06, rel_tol=0.0)"
)]
fn approx_equal(
    a: &Bound<'_, PyAny>,
    b: &Bound<'_, PyAny>,
    abs_tol: Keyword<'_>,
    rel_tol: Keyword<'_>,
) -> PyResult<bool> {
    let abs_tol = tolerance("abs_tol", &abs_tol, DEFAULT_ABS_TOL)?;
    let rel_tol = tolerance("rel_tol", &rel_tol, 0.0)?;
    if let Some(left) = scalar(a)?
        && let Some(right) = scalar(b)?
    {
        return guard("approx_equal", || {
            Ok(util::is_close(left, right, abs_tol, rel_tol))
        });
    }
    let (left, right) = (float_array(a)?, float_array(b)?);
    if left.shape() != right.shape() {
        return Ok(false);
    }
    let (left, right) = (left.readonly(), right.readonly());
    let (left, right) = (left.as_array(), right.as_array());
    guard("approx_equal", || {
        Ok(util::all_close(
            left.iter().copied(),
            right.iter().copied(),
            abs_tol,
            rel_tol,
        ))
    })
}

/// A part count as the caller gave it: one that fits 64 bits with its context value, the
/// decimal digits of a larger int, or a float, which only the checks before `range` accept.
enum Parts {
    Count(i64, ContextValue),
    Huge(String),
    Real(f64),
}

fn read_parts(parts: &Bound<'_, PyAny>) -> PyResult<Parts> {
    if let Ok(flag) = parts.cast::<PyBool>() {
        let is_set = flag.is_true();
        return Ok(Parts::Count(i64::from(is_set), ContextValue::Bool(is_set)));
    }
    if let Ok(float) = parts.cast::<PyFloat>() {
        return Ok(Parts::Real(float.value()));
    }
    match parts.extract::<i64>() {
        Ok(count) => Ok(Parts::Count(count, ContextValue::Int(count))),
        Err(err) if err.is_instance_of::<PyOverflowError>(parts.py()) => {
            let index = parts
                .py()
                .import("operator")?
                .call_method1("index", (parts,))?;
            Ok(Parts::Huge(index.str()?.to_string_lossy().into_owned()))
        }
        Err(err) => Err(err),
    }
}

/// Builds the error `require_size` raises for a float part count outside [0, cap].
fn real_size_error(parts: f64) -> CoreError {
    let shown = py_float_repr(parts);
    let (message, hint) = caps::size_error(&shown, &MAX_POINTS.to_string(), parts < 0.0, "chunks");
    CoreError::new(ErrorKind::Schema, ErrorCode::Schema, message, hint)
        .with_context("what", "chunks")
        .with_context("requested", parts)
        .with_context("cap", MAX_POINTS)
}

/// Reads the total as a float with the part size `total / parts`, divided exactly for an int,
/// and the text an error shows for it.
fn read_total(total: &Bound<'_, PyAny>, parts: i64) -> PyResult<(f64, f64, String)> {
    if let Ok(float) = total.cast::<PyFloat>() {
        let value = float.value();
        let shown = if total.is_exact_instance_of::<PyFloat>() {
            py_float_repr(value)
        } else {
            repr_of(total)
        };
        return Ok((value, value / parts as f64, shown));
    }
    let value: f64 = total.extract()?;
    let step = if total.is_instance_of::<PyInt>() && value.abs() > EXACT_INT_LIMIT {
        total.div(parts)?.extract()?
    } else {
        value / parts as f64
    };
    Ok((value, step, repr_of(total)))
}

/// Splits counts into equal parts that sum to `total`, each cut at an exact boundary.
///
/// Raises SchemaError for fewer than one part, a part count over `MAX_POINTS` and a total that
/// is NaN or infinite.
///
/// >>> from e8s._core.core import chunk_counts
/// >>> chunk_counts(8.0, 4)
/// [2.0, 2.0, 2.0, 2.0]
#[pyfunction]
fn chunk_counts(total: &Bound<'_, PyAny>, parts: &Bound<'_, PyAny>) -> PyResult<Vec<f64>> {
    let parts = read_parts(parts)?;
    let too_few = match &parts {
        Parts::Count(count, shown) if *count < 1 => Some(shown.clone()),
        Parts::Huge(digits) if digits.starts_with('-') => {
            Some(ContextValue::Integer(digits.clone()))
        }
        Parts::Real(value) if *value < 1.0 => Some(ContextValue::Float(*value)),
        _ => None,
    };
    if let Some(shown) = too_few {
        return guard("chunk_counts", || Err(util::too_few_parts(shown)));
    }
    let count = match parts {
        Parts::Count(count, _) => count,
        Parts::Huge(_) | Parts::Real(_) => 1,
    };
    let (value, step, shown) = read_total(total, count)?;
    guard("chunk_counts", || -> CoreResult<()> {
        util::require_finite_total(value, &shown)?;
        match &parts {
            Parts::Huge(digits) => Err(caps::size_beyond_i64(digits, MAX_POINTS, "chunks")),
            Parts::Real(real) if !(0.0..=MAX_POINTS as f64).contains(real) => {
                Err(real_size_error(*real))
            }
            _ => Ok(()),
        }
    })?;
    if let Parts::Real(_) = parts {
        return Err(PyTypeError::new_err(
            "'float' object cannot be interpreted as an integer",
        ));
    }
    guard("chunk_counts", || {
        caps::require_size(count, MAX_POINTS, "chunks")?;
        Ok(util::chunk_counts_by(step, value, count))
    })
}

/// Adds the functions of this module to `module`.
///
/// # Errors
///
/// Returns the error of adding any function.
pub fn add_functions(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(noise_rank, module)?)?;
    module.add_function(wrap_pyfunction!(approx_equal, module)?)?;
    module.add_function(wrap_pyfunction!(chunk_counts, module)?)
}

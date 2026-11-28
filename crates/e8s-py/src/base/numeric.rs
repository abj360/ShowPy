//! numeric --- binds number text, angle wrapping, rounding, facings and the size caps
//!
//! Contains:
//!     format_number(): writes a float in plain decimal notation
//!     normalize_angle(): wraps an angle into [0, 360)
//!     angle_diff(): returns the signed shortest turn between two facings
//!     facing_vector(): converts a facing to a unit direction
//!     vector_facing(): converts a direction to a facing
//!     round_to(): rounds to a multiple of a step, half away from zero
//!     require_size(): rejects a size that is negative or over its cap
//!     add_functions(): adds the functions above to a module

use e8s_core::base::{
    ContextValue, CoreError, ErrorKind, caps,
    numbers::{self, reject_angle, reject_number},
    rounding, units,
};
use pyo3::intern;
use pyo3::prelude::*;
use pyo3::sync::PyOnceLock;
use pyo3::types::{PyBool, PyDict, PyFloat, PyInt, PyType};

use super::host::repr_of;
use crate::errors::{guard, py_int, to_py_err};

static REAL: PyOnceLock<Py<PyType>> = PyOnceLock::new();
static DECIMAL: PyOnceLock<Py<PyType>> = PyOnceLock::new();
static NUMPY_BOOL: PyOnceLock<Py<PyType>> = PyOnceLock::new();
const ERRORS_MODULE: &str = "e8s.core.errors";

/// A number read from a Python object: a float, or the text of a rejected value.
enum Number {
    Float(f64),
    Reject(String),
}

/// Reads a float, an int or any registered real number; bools and other objects are rejected.
fn read_number(value: &Bound<'_, PyAny>) -> PyResult<Number> {
    if let Ok(float) = value.cast::<PyFloat>() {
        return Ok(Number::Float(float.value()));
    }
    if value.is_instance_of::<PyBool>() {
        return Ok(Number::Reject(repr_of(value)));
    }
    let is_real = value.is_instance_of::<PyInt>()
        || value.is_instance(REAL.import(value.py(), "numbers", "Real")?.as_any())?;
    Ok(match value.extract::<f64>() {
        Ok(number) if is_real => Number::Float(number),
        _ => Number::Reject(repr_of(value)),
    })
}

/// Writes a float in plain decimal notation with trailing zeros removed.
///
/// Values that round to zero print as `0`, never `-0`.
///
/// Args:
/// value: A finite number; bools and non-numbers are rejected.
/// max_decimals: Most digits kept after the decimal point, from 0 to 17.
///
/// Returns:
/// text: Digits with an optional sign and point; never an exponent.
///
/// >>> from e8s._core.core import format_number
/// >>> format_number(1234567.5)
/// '1234567.5'
/// >>> format_number(3.0), format_number(1e-9)
/// ('3', '0')
#[pyfunction]
#[pyo3(signature = (value, *, max_decimals = 6))]
fn format_number(value: &Bound<'_, PyAny>, max_decimals: i64) -> PyResult<String> {
    let exact_int = value.is_instance_of::<PyInt>() && !value.is_instance_of::<PyBool>();
    if exact_int && value.extract::<f64>().is_ok_and(f64::is_finite) {
        guard("format_number", || numbers::check_decimals(max_decimals))?;
        return Ok(value.str()?.to_string_lossy().into_owned());
    }
    match read_number(value)? {
        Number::Float(number) => guard("format_number", || {
            numbers::format_number(number, max_decimals)
        }),
        Number::Reject(shown) => guard("format_number", || {
            numbers::check_decimals(max_decimals)?;
            Err(reject_number("format", &shown))
        }),
    }
}

/// Wraps an angle into [0, 360); the result is never -0.0.
///
/// >>> from e8s._core.core import normalize_angle
/// >>> normalize_angle(-90.0)
/// 270.0
#[pyfunction]
fn normalize_angle(deg: &Bound<'_, PyAny>) -> PyResult<f64> {
    if let Ok(float) = deg.cast::<PyFloat>() {
        let degrees = float.value();
        return guard("normalize_angle", || numbers::normalize_angle(degrees));
    }
    match read_number(deg)? {
        Number::Float(number) => guard("normalize_angle", || numbers::normalize_angle(number)),
        Number::Reject(shown) => guard("normalize_angle", || Err(reject_angle(&shown))),
    }
}

/// Returns the signed shortest turn from `a` to `b`, in (-180, 180].
///
/// >>> from e8s._core.core import angle_diff
/// >>> angle_diff(350.0, 10.0)
/// 20.0
#[pyfunction]
fn angle_diff(a: f64, b: f64) -> PyResult<f64> {
    guard("angle_diff", || units::angle_diff(a, b))
}

/// Converts a facing to a unit direction; 0° faces the audience (0, -1).
///
/// >>> from e8s._core.core import facing_vector
/// >>> facing_vector(0.0)
/// (0.0, -1.0)
#[pyfunction]
fn facing_vector(theta: f64) -> PyResult<(f64, f64)> {
    guard("facing_vector", || units::facing_vector(theta))
}

/// Converts a direction to a facing in [0, 360).
///
/// >>> from e8s._core.core import vector_facing
/// >>> vector_facing((1.0, 0.0))
/// 90.0
#[pyfunction]
fn vector_facing(v: &Bound<'_, PyAny>) -> PyResult<f64> {
    let (x, y): (f64, f64) = (v.get_item(0)?.extract()?, v.get_item(1)?.extract()?);
    let outcome = guard("vector_facing", || Ok(units::vector_facing(x, y)))?;
    outcome.map_err(|err| {
        let py = v.py();
        if err.kind() != ErrorKind::ZeroLength {
            return to_py_err(py, &err);
        }
        let context = PyDict::new(py);
        let built = context
            .set_item("vector", v)
            .and_then(|()| raise_class(py, ErrorKind::ZeroLength.class_name(), &err, &context));
        built.unwrap_or_else(|failure| failure)
    })
}

/// Builds the exception of a core error with another class and context.
fn raise_class(
    py: Python<'_>,
    class_name: &str,
    err: &CoreError,
    context: &Bound<'_, PyDict>,
) -> PyResult<PyErr> {
    let class = py.import(ERRORS_MODULE)?.getattr(class_name)?;
    build_with(&class, err, context)
}

fn build_with(
    class: &Bound<'_, PyAny>,
    err: &CoreError,
    context: &Bound<'_, PyDict>,
) -> PyResult<PyErr> {
    let kwargs = PyDict::new(class.py());
    kwargs.set_item("hint", err.hint())?;
    kwargs.set_item("context", context)?;
    Ok(PyErr::from_value(
        class.call((err.message(),), Some(&kwargs))?,
    ))
}

/// Rounds to a multiple of `step`, half away from zero, for display.
///
/// Raises SchemaError for a bool, a non-number, a non-finite value, a step that is not finite
/// and positive, and a quotient too long for 28 decimal digits. Every result that rounds to
/// zero is `0.0`, never `-0.0`.
///
/// Args:
/// value: The number to round.
/// step: Size of the grid to round onto; 0.01 when omitted.
///
/// Returns:
/// rounded: The nearest multiple of `step`.
///
/// >>> from e8s._core.core import round_to
/// >>> round_to(2.345)
/// 2.35
#[pyfunction]
#[pyo3(signature = (value, step = None))]
#[pyo3(text_signature = "(value, step=0.01)")]
fn round_to(value: &Bound<'_, PyAny>, step: Option<&Bound<'_, PyAny>>) -> PyResult<f64> {
    match read_number(value)? {
        Number::Reject(shown) => guard("round_to", || Err(reject_number("round", &shown))),
        Number::Float(number) => {
            let step = match step {
                Some(step) if number.is_finite() => step.extract::<f64>()?,
                _ => rounding::DEFAULT_STEP,
            };
            guard("round_to", || rounding::round_to(number, step))
        }
    }
}

/// Rejects a requested size that is negative or over its cap, before the caller allocates it.
///
/// Args:
/// requested: Size the caller is about to allocate, such as width times height; a negative
/// size is rejected too.
/// cap: Largest size allowed.
/// what: Name of the thing being sized, used in the message.
/// error: Error class to raise.
///
/// >>> from e8s._core.core import require_size
/// >>> require_size(1_000, 1_000_000, what="points")
/// >>> require_size(1_000_001, 1_000_000, what="points")  # doctest: +ELLIPSIS
/// Traceback (most recent call last):
/// e8s.core.errors.SchemaError: SCHEMA: points would need 1000001, over the cap of 1000000 ...
#[pyfunction]
#[pyo3(signature = (requested, cap, *, what, error = None))]
#[pyo3(text_signature = "(requested, cap, *, what, error=...)")]
fn require_size(
    requested: &Bound<'_, PyAny>,
    cap: i64,
    what: &str,
    error: Option<&Bound<'_, PyAny>>,
) -> PyResult<()> {
    let py = requested.py();
    let Ok(size) = requested.extract::<i64>() else {
        if requested.is_instance(NUMPY_BOOL.import(py, "numpy", "bool_")?.as_any())? {
            let flag = requested.call_method0(intern!(py, "item"))?;
            return require_size(&flag, cap, what, error);
        }
        return require_huge_size(requested, cap, what, error);
    };
    let outcome = guard("require_size", || Ok(caps::require_size(size, cap, what)))?;
    if outcome.is_err() && requested.is_instance_of::<PyBool>() {
        return require_huge_size(requested, cap, what, error);
    }
    outcome.map_err(|err| match error {
        None => to_py_err(py, &err),
        Some(class) => {
            let context =
                context_of(py, &err).and_then(|context| build_with(class, &err, &context));
            context.unwrap_or_else(|failure| failure)
        }
    })
}

fn context_of<'py>(py: Python<'py>, err: &CoreError) -> PyResult<Bound<'py, PyDict>> {
    let context = PyDict::new(py);
    for (key, value) in err.context() {
        match value {
            ContextValue::Bool(flag) => context.set_item(key, *flag),
            ContextValue::Int(number) => context.set_item(key, *number),
            ContextValue::Integer(digits) => context.set_item(key, py_int(context.py(), digits)?),
            ContextValue::Float(number) => context.set_item(key, *number),
            ContextValue::Text(text) => context.set_item(key, text),
        }?;
    }
    Ok(context)
}

/// Tells whether a value that is not an int is a real number, as v0.4.0's comparisons took it.
fn is_real(requested: &Bound<'_, PyAny>) -> PyResult<bool> {
    Ok(requested.is_instance_of::<PyFloat>()
        || requested.is_instance(REAL.import(requested.py(), "numbers", "Real")?.as_any())?
        || is_decimal(requested)?)
}

fn is_decimal(requested: &Bound<'_, PyAny>) -> PyResult<bool> {
    requested.is_instance(
        DECIMAL
            .import(requested.py(), "decimal", "Decimal")?
            .as_any(),
    )
}

/// Tells whether a real number is NaN; a Decimal NaN refuses ordering, so it is asked directly.
fn is_nan(requested: &Bound<'_, PyAny>) -> PyResult<bool> {
    if is_decimal(requested)? {
        return requested
            .call_method0(intern!(requested.py(), "is_nan"))?
            .is_truthy();
    }
    Ok(false)
}

/// Rejects a size that is not a 64-bit int.
///
/// An int beyond 64 bits is over every cap or below zero, and a bool over the cap shows as
/// `True`. A real number outside [0, cap], NaN included, gets the same error, as v0.4.0 compared
/// it; a Decimal counts as real, and a Decimal NaN is over the cap as a float NaN is. A real
/// number in range, such as 3.5, 3.0 or `Decimal(3)`, gets the same error class asking for a
/// whole-number int: a size is a count. Any other value raises the TypeError v0.4.0's comparison
/// raised.
fn require_huge_size(
    requested: &Bound<'_, PyAny>,
    cap: i64,
    what: &str,
    error: Option<&Bound<'_, PyAny>>,
) -> PyResult<()> {
    let py = requested.py();
    let is_int = requested.is_instance_of::<PyInt>();
    if !is_int && !is_real(requested)? {
        return Err(requested.extract::<i64>().err().unwrap_or_else(|| {
            pyo3::exceptions::PyTypeError::new_err("requested must be an int")
        }));
    }
    let shown = requested.str()?.to_string_lossy().into_owned();
    let nan = is_nan(requested)?;
    let in_range = !is_int && !nan && requested.ge(0)? && requested.le(cap)?;
    let (message, hint) = if in_range {
        caps::whole_number_error(&shown, what)
    } else {
        caps::size_error(&shown, &cap.to_string(), !nan && requested.lt(0)?, what)
    };
    let context = PyDict::new(py);
    context.set_item("what", what)?;
    context.set_item("requested", requested)?;
    context.set_item("cap", cap)?;
    let class = match error {
        Some(class) => class.clone(),
        None => py.import(ERRORS_MODULE)?.getattr("SchemaError")?,
    };
    let err = CoreError::new(
        ErrorKind::Schema,
        e8s_core::base::ErrorCode::Schema,
        message,
        hint,
    );
    Err(build_with(&class, &err, &context)?)
}

/// Adds the functions of this module to `module`.
///
/// # Errors
///
/// Returns the error of adding any function.
pub fn add_functions(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(format_number, module)?)?;
    module.add_function(wrap_pyfunction!(normalize_angle, module)?)?;
    module.add_function(wrap_pyfunction!(angle_diff, module)?)?;
    module.add_function(wrap_pyfunction!(facing_vector, module)?)?;
    module.add_function(wrap_pyfunction!(vector_facing, module)?)?;
    module.add_function(wrap_pyfunction!(round_to, module)?)?;
    module.add_function(wrap_pyfunction!(require_size, module)?)
}

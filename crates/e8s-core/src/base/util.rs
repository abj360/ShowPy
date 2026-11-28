//! util --- small shared numeric helpers: noise ranks, float comparison and count chunks
//!
//! Contains:
//!     NOISE_RANK_DIGITS: the decimals a float that decides an order is rounded to
//!     DEFAULT_ABS_TOL: the absolute tolerance of approx_equal when none is given
//!     noise_rank(): rounds a float that decides an order so 1-ulp noise ties
//!     require_tolerance(): rejects a tolerance that is negative or NaN
//!     is_close(): compares two floats within absolute and relative tolerances
//!     all_close(): compares two equally long float sequences element by element
//!     too_few_parts(): builds the error for a part count below 1
//!     require_parts(): rejects a part count below 1
//!     require_finite_total(): rejects a NaN or infinite total
//!     chunk_counts(): splits counts into equal parts that sum exactly
//!     chunk_counts_by(): splits counts by a given part size, ending exactly at the total

use super::caps::{MAX_POINTS, require_size};
use super::error::{ContextValue, CoreError, CoreResult};
use super::error_codes::{ErrorCode, ErrorKind};
use super::numbers::py_float_repr;

/// Floats that decide a count or an order rank at this many decimals (a nanometer, a
/// nanocount): their last bits are float noise that differs between platforms (libm, fused
/// multiply-add).
pub const NOISE_RANK_DIGITS: usize = 9;
/// The absolute tolerance of approx_equal when none is given.
pub const DEFAULT_ABS_TOL: f64 = 1e-6;

/// Rounds a float that decides an order to NOISE_RANK_DIGITS decimals, so noise ties.
///
/// Rounds the exact binary value half to even and reads the decimal back, as Python's builtin
/// `round(value, 9)` does; scaling by 1e9 instead can land 1 ulp off the correctly rounded value
/// and so reintroduce the noise. NaN and infinities are returned unchanged.
#[must_use]
pub fn noise_rank(value: f64) -> f64 {
    if !value.is_finite() {
        return value;
    }
    format!("{:.*}", NOISE_RANK_DIGITS, value)
        .parse::<f64>()
        .unwrap_or(value)
}

/// Rejects a tolerance that is negative or NaN.
///
/// # Errors
///
/// Returns a SchemaError naming the tolerance and showing `shown`, the caller's repr of it.
pub fn require_tolerance(name: &'static str, value: f64, shown: &str) -> CoreResult<()> {
    if value >= 0.0 {
        return Ok(());
    }
    Err(CoreError::new(
        ErrorKind::Schema,
        ErrorCode::Schema,
        format!("{name} must be 0 or more, got {shown}"),
        format!("pass {name} as a number 0 or more, such as 1e-6"),
    )
    .with_context(name, shown))
}

/// Tells whether `a` is within `abs_tol + rel_tol * |b|` of `b`; equal infinities are close and
/// NaN is close to nothing.
#[must_use]
pub fn is_close(a: f64, b: f64, abs_tol: f64, rel_tol: f64) -> bool {
    (a - b).abs() <= abs_tol + rel_tol * b.abs() || a == b
}

/// Tells whether every element of `a` is close to its element of `b`; sequences of different
/// lengths are never close.
#[must_use]
pub fn all_close(
    a: impl ExactSizeIterator<Item = f64>,
    b: impl ExactSizeIterator<Item = f64>,
    abs_tol: f64,
    rel_tol: f64,
) -> bool {
    a.len() == b.len() && a.zip(b).all(|(x, y)| is_close(x, y, abs_tol, rel_tol))
}

/// Builds the error for a part count below 1; `parts` is the count as the caller gave it.
#[must_use]
pub fn too_few_parts(parts: impl Into<ContextValue>) -> CoreError {
    CoreError::new(
        ErrorKind::Schema,
        ErrorCode::Schema,
        "parts must be at least 1",
        "pass parts >= 1",
    )
    .with_context("parts", parts)
}

/// Rejects a part count below 1.
///
/// # Errors
///
/// Returns the SchemaError of `too_few_parts`.
pub fn require_parts(parts: i64) -> CoreResult<()> {
    if parts < 1 {
        return Err(too_few_parts(parts));
    }
    Ok(())
}

/// Rejects a NaN or infinite total; `shown` is the caller's repr of it.
///
/// # Errors
///
/// Returns a SchemaError showing the total.
pub fn require_finite_total(total: f64, shown: &str) -> CoreResult<()> {
    if total.is_finite() {
        return Ok(());
    }
    Err(CoreError::new(
        ErrorKind::Schema,
        ErrorCode::Schema,
        format!("total must be finite, got {shown}"),
        "pass a finite number of counts",
    )
    .with_context("total", shown))
}

/// Splits `total` counts into `parts` equal parts that sum to it, each cut at an exact boundary.
///
/// # Errors
///
/// Returns a SchemaError for fewer than one part, a part count over `MAX_POINTS` and a total
/// that is NaN or infinite.
pub fn chunk_counts(total: f64, parts: i64) -> CoreResult<Vec<f64>> {
    require_parts(parts)?;
    require_finite_total(total, &py_float_repr(total))?;
    require_size(parts, MAX_POINTS, "chunks")?;
    Ok(chunk_counts_by(total / parts as f64, total, parts))
}

/// Splits counts at the boundaries `step * i` for `i` in 1..parts and ends at `total`, so the
/// parts sum exactly to `total`. The caller has checked `parts` and `total`.
#[must_use]
pub fn chunk_counts_by(step: f64, total: f64, parts: i64) -> Vec<f64> {
    let mut chunks = Vec::with_capacity(usize::try_from(parts).unwrap_or(0));
    let mut start = 0.0;
    for index in 1..parts {
        let stop = step * index as f64;
        chunks.push(stop - start);
        start = stop;
    }
    chunks.push(total - start);
    chunks
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn noise_rank_rounds_ties_to_even_like_python_round() {
        assert_eq!(noise_rank(-0.599_999_999_999_999_9), noise_rank(-0.6));
        // Python: round(1 / 1024, 9) and round(3 / 1024, 9)
        assert_eq!(noise_rank(0.000_976_562_5), 0.000_976_562);
        assert_eq!(noise_rank(0.002_929_687_5), 0.002_929_688);
        assert_eq!(noise_rank(1e300), 1e300);
        assert!(noise_rank(-1e-12).is_sign_negative());
        assert!(noise_rank(f64::NAN).is_nan());
    }

    #[test]
    fn tolerances_below_zero_or_nan_are_refused() {
        assert!(require_tolerance("abs_tol", 0.0, "0.0").is_ok());
        let Err(err) = require_tolerance("rel_tol", f64::NAN, "nan") else {
            unreachable!("a NaN tolerance passed");
        };
        assert_eq!(err.message(), "rel_tol must be 0 or more, got nan");
        assert_eq!(
            err.hint(),
            "pass rel_tol as a number 0 or more, such as 1e-6"
        );
    }

    #[test]
    fn closeness_treats_equal_infinities_as_equal_and_nan_as_unequal() {
        assert!(is_close(f64::INFINITY, f64::INFINITY, 0.0, 0.0));
        assert!(!is_close(f64::INFINITY, f64::NEG_INFINITY, 1.0, 0.0));
        assert!(!is_close(f64::NAN, f64::NAN, 1.0, 1.0));
        assert!(is_close(1e9, 1e9 + 1.0, 0.0, 1e-6));
        assert!(!all_close(
            [1.0].into_iter(),
            [1.0, 2.0].into_iter(),
            1.0,
            0.0
        ));
    }

    #[test]
    fn chunks_sum_exactly_and_refuse_bad_input() {
        assert_eq!(chunk_counts(8.0, 4), Ok(vec![2.0, 2.0, 2.0, 2.0]));
        let chunks = chunk_counts(0.700_000_000_000_000_1, 7).unwrap_or_default();
        assert_eq!(chunks.len(), 7);
        assert!(chunk_counts(1.7e308, 3).is_ok_and(|parts| parts.iter().all(|p| p.is_finite())));
        assert!(chunk_counts(8.0, 0).is_err());
        assert!(chunk_counts(f64::NAN, 4).is_err());
        assert!(chunk_counts(8.0, MAX_POINTS + 1).is_err());
    }
}

//! numbers --- plain-decimal number text, angle wrapping and Python's float text
//!
//! Contains:
//!     TURN_DEG: the degrees of one full turn
//!     DEFAULT_DECIMALS: the decimals `format_number` keeps when none are given
//!     MAX_DECIMALS: the most decimals `format_number` keeps
//!     py_float_repr(): writes a float as Python's `repr` does
//!     float_digits(): splits a finite float into its shortest digits and decimal exponent
//!     check_decimals(): checks that a decimals count is from 0 to 17
//!     format_number(): writes a float in plain decimal notation, never in exponent form
//!     reject_number(): builds the error for a value that is not a finite number
//!     normalize_angle(): wraps an angle into [0, 360) and rejects NaN and infinity

use super::error::{CoreError, CoreResult};
use super::error_codes::{ErrorCode, ErrorKind};

/// The degrees of one full turn.
pub const TURN_DEG: f64 = 360.0;
/// The decimals `format_number` keeps when none are given.
pub const DEFAULT_DECIMALS: i64 = 6;
/// The most decimals `format_number` keeps.
pub const MAX_DECIMALS: i64 = 17;
const REPR_EXPONENT_LOW: i64 = -4;
const REPR_EXPONENT_HIGH: i64 = 16;

/// Splits a finite, non-zero float into its shortest round-trip digits and decimal exponent.
///
/// The value is `0.DIGITS * 10^point` with `point` the position of the decimal point, as
/// Python's `repr` reads it; the digits hold no trailing zero.
#[must_use]
pub fn float_digits(value: f64) -> (String, i64) {
    let scientific = format!("{:e}", value.abs());
    let (mantissa, exponent) = scientific.split_once('e').unwrap_or((&scientific, "0"));
    let digits: String = mantissa.chars().filter(char::is_ascii_digit).collect();
    (digits, exponent.parse::<i64>().unwrap_or(0) + 1)
}

/// Writes a float as Python's `repr` does: `nan`, `inf`, `-0.0`, `1e-05`, `1.5e+16`, `0.1`.
#[must_use]
pub fn py_float_repr(value: f64) -> String {
    if value.is_nan() {
        return "nan".to_owned();
    }
    let sign = if value.is_sign_negative() { "-" } else { "" };
    if value.is_infinite() {
        return format!("{sign}inf");
    }
    if value == 0.0 {
        return format!("{sign}0.0");
    }
    let (digits, point) = float_digits(value);
    let count = i64::try_from(digits.len()).unwrap_or(i64::MAX);
    if point <= REPR_EXPONENT_LOW || point > REPR_EXPONENT_HIGH {
        let (first, rest) = digits.split_at(1);
        let fraction = if rest.is_empty() {
            String::new()
        } else {
            format!(".{rest}")
        };
        let exponent = point - 1;
        let exponent_sign = if exponent < 0 { '-' } else { '+' };
        return format!(
            "{sign}{first}{fraction}e{exponent_sign}{:02}",
            exponent.abs()
        );
    }
    let zeros = |n: i64| "0".repeat(usize::try_from(n).unwrap_or(0));
    if point <= 0 {
        return format!("{sign}0.{}{digits}", zeros(-point));
    }
    if point >= count {
        return format!("{sign}{digits}{}.0", zeros(point - count));
    }
    let (whole, fraction) = digits.split_at(usize::try_from(point).unwrap_or(0));
    format!("{sign}{whole}.{fraction}")
}

/// Builds the error for a value that is not a finite number, naming it as `shown`.
///
/// The text of `what` completes "cannot ...", so `format` gives "cannot format x".
#[must_use]
pub fn reject_number(what: &str, shown: &str) -> CoreError {
    CoreError::new(
        ErrorKind::Schema,
        ErrorCode::Schema,
        format!("cannot {what} {shown}"),
        "pass a finite number",
    )
    .with_context("value", shown)
}

/// Checks that `max_decimals` is from 0 to 17 and returns it as a count.
///
/// # Errors
///
/// Returns a SchemaError naming `max_decimals` when it is out of range.
pub fn check_decimals(max_decimals: i64) -> CoreResult<usize> {
    match usize::try_from(max_decimals) {
        Ok(decimals) if max_decimals <= MAX_DECIMALS => Ok(decimals),
        _ => Err(CoreError::new(
            ErrorKind::Schema,
            ErrorCode::Schema,
            format!("max_decimals must be 0 to {MAX_DECIMALS}, got {max_decimals}"),
            format!("pass max_decimals from 0 to {MAX_DECIMALS}"),
        )
        .with_context("max_decimals", max_decimals)),
    }
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
/// # Errors
///
/// Returns a SchemaError when `max_decimals` is outside 0 to 17 or `value` is not finite.
pub fn format_number(value: f64, max_decimals: i64) -> CoreResult<String> {
    let decimals = check_decimals(max_decimals)?;
    if !value.is_finite() {
        return Err(reject_number("format", &py_float_repr(value)));
    }
    let mut text = format!("{value:.decimals$}");
    if text.contains('.') {
        text.truncate(text.trim_end_matches('0').trim_end_matches('.').len());
    }
    if text == "-0" {
        return Ok("0".to_owned());
    }
    Ok(text)
}

/// Builds the error for an angle that is not a finite number, naming it as `shown`.
#[must_use]
pub fn reject_angle(shown: &str) -> CoreError {
    CoreError::new(
        ErrorKind::Schema,
        ErrorCode::Schema,
        format!("angle must be a finite number, got {shown}"),
        "pass a finite number of degrees",
    )
    .with_context("deg", shown)
}

/// Wraps an angle into [0, 360); the result is never -0.0.
///
/// # Errors
///
/// Returns a SchemaError when `deg` is NaN or infinite.
pub fn normalize_angle(deg: f64) -> CoreResult<f64> {
    if !deg.is_finite() {
        return Err(reject_angle(&py_float_repr(deg)));
    }
    let remainder = libm::fmod(deg, TURN_DEG);
    let wrapped = if remainder < 0.0 {
        remainder + TURN_DEG
    } else {
        remainder
    };
    Ok(if wrapped >= TURN_DEG {
        0.0
    } else {
        wrapped + 0.0
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn py_float_repr_matches_python() {
        let cases = [
            (0.1, "0.1"),
            (1e-5, "1e-05"),
            (1e16, "1e+16"),
            (1e15, "1000000000000000.0"),
            (1.5e300, "1.5e+300"),
            (-2.5, "-2.5"),
            (0.0001, "0.0001"),
            (123_456.789, "123456.789"),
            (5e-324, "5e-324"),
            (f64::MAX, "1.7976931348623157e+308"),
        ];
        for (value, text) in cases {
            assert_eq!(py_float_repr(value), text);
        }
        assert_eq!(py_float_repr(-0.0), "-0.0");
        assert_eq!(py_float_repr(f64::NEG_INFINITY), "-inf");
    }

    #[test]
    fn format_number_trims_and_never_writes_negative_zero() {
        assert_eq!(format_number(-1e-9, 6), Ok("0".to_owned()));
        assert_eq!(format_number(2.5, 0), Ok("2".to_owned()));
        assert_eq!(format_number(0.125, 2), Ok("0.12".to_owned()));
        assert_eq!(format_number(1e15, 6), Ok("1000000000000000".to_owned()));
    }

    #[test]
    fn format_number_rejects_bad_decimals_before_the_value() {
        let Err(err) = format_number(f64::NAN, 18) else {
            unreachable!("18 decimals passed");
        };
        assert!(err.message().starts_with("max_decimals must be 0 to 17"));
    }

    #[test]
    fn normalize_angle_wraps_and_rejects_non_finite() {
        assert_eq!(normalize_angle(-90.0), Ok(270.0));
        assert_eq!(normalize_angle(-0.0).map(f64::is_sign_negative), Ok(false));
        assert_eq!(normalize_angle(-1e-20), Ok(0.0));
        assert!(normalize_angle(f64::INFINITY).is_err());
    }
}

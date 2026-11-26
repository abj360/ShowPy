//! rounding --- rounds a number to a step, half away from zero, in 28-digit decimal arithmetic
//!
//! Contains:
//!     DECIMAL_DIGITS: the significant digits every decimal result keeps
//!     DEFAULT_STEP: the step `round_to` rounds onto when the caller gives none
//!     round_to(): rounds to a multiple of `step`, half away from zero, for display

use super::error::{CoreError, CoreResult};
use super::error_codes::{ErrorCode, ErrorKind};
use super::numbers::{float_digits, py_float_repr, reject_number};

/// The significant digits every decimal result keeps, as Python's default decimal context does.
pub const DECIMAL_DIGITS: usize = 28;
/// The step `round_to` rounds onto when the caller gives none.
pub const DEFAULT_STEP: f64 = 0.01;
const DIGIT_BASE: u8 = 10;

/// A decimal result of at most `DECIMAL_DIGITS` digits: `coefficient * 10^exponent`.
struct Decimal {
    coefficient: u128,
    exponent: i64,
}

fn digits_of(mut number: u128) -> Vec<u8> {
    let mut digits = Vec::new();
    while number > 0 {
        digits.push(u8::try_from(number % u128::from(DIGIT_BASE)).unwrap_or(0));
        number /= u128::from(DIGIT_BASE);
    }
    digits.reverse();
    digits
}

fn number_of(digits: &[u8]) -> u128 {
    digits.iter().fold(0, |sum, digit| {
        sum * u128::from(DIGIT_BASE) + u128::from(*digit)
    })
}

/// Adds one to the last digit of a most-significant-first digit list, carrying leftwards.
///
/// Returns true when the carry left the list one digit longer (all nines became 1 and zeros).
fn increment(digits: &mut Vec<u8>) -> bool {
    for digit in digits.iter_mut().rev() {
        if *digit + 1 < DIGIT_BASE {
            *digit += 1;
            return false;
        }
        *digit = 0;
    }
    digits.insert(0, 1);
    true
}

/// Decides whether the dropped part rounds the kept digits up, half to even.
fn rounds_up(kept: &[u8], dropped: &[u8]) -> bool {
    let (first, rest) = dropped.split_first().map_or((0, &[][..]), |(f, r)| (*f, r));
    let half = DIGIT_BASE / 2;
    first > half
        || (first == half
            && (rest.iter().any(|d| *d != 0) || kept.last().is_some_and(|d| d % 2 == 1)))
}

/// Keeps the first `DECIMAL_DIGITS` digits, rounded half to even; returns the digits cut off.
fn round_digits(digits: &mut Vec<u8>, exponent: &mut i64) {
    if digits.len() <= DECIMAL_DIGITS {
        return;
    }
    let dropped = digits.split_off(DECIMAL_DIGITS);
    *exponent += i64::try_from(dropped.len()).unwrap_or(0);
    if rounds_up(digits, &dropped) && increment(digits) {
        digits.pop();
        *exponent += 1;
    }
}

/// Divides two positive decimals `c1 * 10^e1 / c2 * 10^e2`, keeping 28 significant digits.
fn divide(numerator: &Decimal, denominator: &Decimal) -> Decimal {
    let base = u128::from(DIGIT_BASE);
    let mut digits = digits_of(numerator.coefficient / denominator.coefficient);
    let mut remainder = numerator.coefficient % denominator.coefficient;
    let mut fraction_digits: i64 = 0;
    while digits.len() < DECIMAL_DIGITS && remainder != 0 {
        remainder *= base;
        let digit = u8::try_from(remainder / denominator.coefficient).unwrap_or(0);
        remainder %= denominator.coefficient;
        fraction_digits += 1;
        if !digits.is_empty() || digit != 0 {
            digits.push(digit);
        }
    }
    let mut exponent = numerator.exponent - denominator.exponent - fraction_digits;
    if remainder != 0 {
        let twice = remainder * 2;
        let up = twice > denominator.coefficient
            || (twice == denominator.coefficient && digits.last().is_some_and(|d| d % 2 == 1));
        if up && increment(&mut digits) {
            digits.pop();
            exponent += 1;
        }
    }
    Decimal {
        coefficient: number_of(&digits),
        exponent,
    }
}

/// Rounds a decimal to an integer, half away from zero; None when it needs over 28 digits.
fn quantize_to_integer(quotient: &Decimal) -> Option<u128> {
    if quotient.exponent >= 0 {
        let integer_digits = digits_of(quotient.coefficient).len()
            + usize::try_from(quotient.exponent).unwrap_or(usize::MAX);
        if integer_digits > DECIMAL_DIGITS {
            return None;
        }
        let scale = 10_u128.pow(u32::try_from(quotient.exponent).unwrap_or(0));
        return Some(quotient.coefficient * scale);
    }
    let cut = u32::try_from(-quotient.exponent).unwrap_or(u32::MAX);
    if cut as usize > DECIMAL_DIGITS {
        return Some(0);
    }
    let scale = 10_u128.pow(cut);
    let whole = quotient.coefficient / scale;
    Some(whole + u128::from(quotient.coefficient % scale * 2 >= scale))
}

/// Multiplies two integers into their decimal digits, most significant first.
fn product_digits(left: u128, right: u128) -> Vec<u8> {
    let (a, b) = (digits_of(left), digits_of(right));
    let mut cells = vec![0_u32; a.len() + b.len()];
    for (i, x) in a.iter().rev().enumerate() {
        for (j, y) in b.iter().rev().enumerate() {
            cells[i + j] += u32::from(*x) * u32::from(*y);
        }
    }
    let base = u32::from(DIGIT_BASE);
    let mut carry = 0;
    for cell in &mut cells {
        let total = *cell + carry;
        *cell = total % base;
        carry = total / base;
    }
    let mut digits: Vec<u8> = cells
        .iter()
        .rev()
        .map(|cell| u8::try_from(*cell).unwrap_or(0))
        .collect();
    let leading = digits.iter().take_while(|d| **d == 0).count();
    digits.drain(..leading);
    digits
}

fn too_many_digits(value: f64, step: f64) -> CoreError {
    let (shown_value, shown_step) = (py_float_repr(value), py_float_repr(step));
    CoreError::new(
        ErrorKind::Schema,
        ErrorCode::Schema,
        format!("cannot round {shown_value} to a step of {shown_step}: too many digits"),
        "use a larger step or a smaller value",
    )
    .with_context("value", shown_value)
    .with_context("step", shown_step)
}

fn decimal_of(value: f64) -> Decimal {
    let (digits, point) = float_digits(value);
    let exponent = point - i64::try_from(digits.len()).unwrap_or(0);
    Decimal {
        coefficient: digits.parse().unwrap_or(0),
        exponent,
    }
}

/// Rounds to a multiple of `step`, half away from zero, for display.
///
/// The quotient and the product use 28-digit decimal arithmetic on the shortest decimal text of
/// each float, so `2.345` rounds to `2.35` at step 0.01. Every result that rounds to zero is
/// `0.0`, never `-0.0`.
///
/// # Errors
///
/// Returns a SchemaError for a non-finite value, a step that is not finite and positive, and a
/// quotient too long for 28 decimal digits.
pub fn round_to(value: f64, step: f64) -> CoreResult<f64> {
    if !value.is_finite() {
        return Err(reject_number("round", &py_float_repr(value)));
    }
    if !step.is_finite() || step <= 0.0 {
        let shown = py_float_repr(step);
        return Err(CoreError::new(
            ErrorKind::Schema,
            ErrorCode::Schema,
            format!("step must be finite and above 0, got {shown}"),
            "pass a step such as 0.01",
        )
        .with_context("step", shown));
    }
    if value == 0.0 {
        return Ok(0.0);
    }
    let unit = decimal_of(step);
    let quotient = divide(&decimal_of(value), &unit);
    let steps = quantize_to_integer(&quotient).ok_or_else(|| too_many_digits(value, step))?;
    if steps == 0 {
        return Ok(0.0);
    }
    let mut digits = product_digits(steps, unit.coefficient);
    let mut exponent = unit.exponent;
    round_digits(&mut digits, &mut exponent);
    let text: String = digits
        .iter()
        .map(|digit| char::from(b'0' + digit))
        .collect();
    let magnitude = format!("{text}e{exponent}")
        .parse::<f64>()
        .unwrap_or(f64::NAN);
    Ok(if value < 0.0 { -magnitude } else { magnitude } + 0.0)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rounds_half_away_from_zero() {
        assert_eq!(round_to(2.345, 0.01), Ok(2.35));
        assert_eq!(round_to(-2.345, 0.01), Ok(-2.35));
        assert_eq!(round_to(2.5, 1.0), Ok(3.0));
        assert_eq!(round_to(0.125, 0.25), Ok(0.25));
    }

    #[test]
    fn every_zero_is_positive() {
        for value in [-0.0, -0.001, 0.001, -1e-300] {
            let rounded = round_to(value, 0.01);
            assert_eq!(rounded.map(f64::is_sign_negative), Ok(false));
        }
    }

    #[test]
    fn a_quotient_over_28_digits_is_rejected() {
        let Err(err) = round_to(1e300, 0.01) else {
            unreachable!("1e300 rounded");
        };
        assert!(err.message().ends_with("too many digits"));
    }

    #[test]
    fn bad_values_and_steps_are_rejected() {
        assert!(round_to(f64::INFINITY, 0.01).is_err());
        for step in [-0.01, 0.0, f64::NAN, f64::INFINITY] {
            assert!(round_to(1.0, step).is_err());
        }
    }

    #[test]
    fn extreme_values_and_steps_neither_panic_nor_change_the_value() {
        assert_eq!(round_to(5e-324, 5e-324), Ok(5e-324));
        assert_eq!(round_to(1e308, 1e308), Ok(1e308));
        assert_eq!(round_to(f64::MAX, f64::MAX), Ok(f64::MAX));
        assert_eq!(round_to(1e-300, 1.0), Ok(0.0));
        assert!(round_to(f64::MAX, 1.0).is_err());
        assert!(round_to(f64::MAX, 1e300).is_ok_and(f64::is_finite));
    }

    #[test]
    fn product_digits_multiply_exactly() {
        assert_eq!(number_of(&product_digits(99, 99)), 9801);
        assert_eq!(product_digits(10_u128.pow(27), 10_u128.pow(17)).len(), 45);
    }
}

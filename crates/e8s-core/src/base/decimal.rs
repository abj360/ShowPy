//! decimal --- non-negative integers held as ASCII digits, for ID numbers of any length
//!
//! Contains:
//!     canonical(): drops leading zeros, keeping one digit for zero
//!     is_padded(): tells whether digits carry a leading zero
//!     compare(): orders two digit runs by value
//!     increment(): adds one to canonical digits in place
//!     count_between(): returns `high - low + 1` for canonical `low <= high`
//!     padded_to(): writes canonical digits zero-padded to a width

use std::cmp::Ordering;

const ZERO: &[u8] = b"0";

/// Drops leading zeros, keeping one digit for zero.
#[must_use]
pub fn canonical(digits: &[u8]) -> &[u8] {
    let zeros = digits.iter().take_while(|&&byte| byte == b'0').count();
    if zeros == digits.len() {
        return ZERO;
    }
    &digits[zeros..]
}

/// Tells whether digits carry a leading zero, as `T01` does and `T0` does not.
#[must_use]
pub fn is_padded(digits: &[u8]) -> bool {
    digits.len() > 1 && digits.first() == Some(&b'0')
}

/// Orders two digit runs by value.
#[must_use]
pub fn compare(left: &[u8], right: &[u8]) -> Ordering {
    let (left, right) = (canonical(left), canonical(right));
    left.len().cmp(&right.len()).then_with(|| left.cmp(right))
}

/// Adds one to canonical digits in place.
pub fn increment(digits: &mut Vec<u8>) {
    for digit in digits.iter_mut().rev() {
        if *digit == b'9' {
            *digit = b'0';
        } else {
            *digit += 1;
            return;
        }
    }
    digits.insert(0, b'1');
}

/// Returns the canonical digits of `high - low + 1` for canonical `low <= high`.
#[must_use]
pub fn count_between(low: &[u8], high: &[u8]) -> Vec<u8> {
    let mut difference = Vec::with_capacity(high.len() + 1);
    let mut borrow = 0_u8;
    for (index, &digit) in high.iter().rev().enumerate() {
        let subtrahend = low
            .len()
            .checked_sub(index + 1)
            .map_or(0, |at| low[at] - b'0')
            + borrow;
        let value = digit - b'0';
        let (result, next) = if value >= subtrahend {
            (value - subtrahend, 0)
        } else {
            (value + 10 - subtrahend, 1)
        };
        difference.push(b'0' + result);
        borrow = next;
    }
    difference.reverse();
    let mut count = canonical(&difference).to_vec();
    increment(&mut count);
    count
}

/// Writes canonical digits to `out`, zero-padded on the left to `width` digits.
pub fn padded_to(digits: &[u8], width: usize, out: &mut Vec<u8>) {
    out.extend(std::iter::repeat_n(
        b'0',
        width.saturating_sub(digits.len()),
    ));
    out.extend_from_slice(digits);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn digits_compare_and_count_by_value() {
        assert_eq!(canonical(b"0007"), b"7");
        assert_eq!(canonical(b"000"), b"0");
        assert_eq!(compare(b"010", b"9"), Ordering::Greater);
        assert_eq!(count_between(b"1", b"10000"), b"10000".to_vec());
        assert_eq!(count_between(b"7", b"7"), b"1".to_vec());
        assert_eq!(
            count_between(b"99999999999999999999", b"100000000000000000003"),
            b"5".to_vec()
        );
        assert_eq!(
            count_between(b"1", b"100000000000000000000000"),
            b"100000000000000000000000".to_vec()
        );
    }

    #[test]
    fn increments_carry_and_padding_fills_to_the_width() {
        let mut digits = b"999".to_vec();
        increment(&mut digits);
        assert_eq!(digits, b"1000".to_vec());
        let mut out = Vec::new();
        padded_to(b"8", 3, &mut out);
        padded_to(b"1234", 2, &mut out);
        assert_eq!(out, b"0081234".to_vec());
        assert!(is_padded(b"01") && !is_padded(b"0") && !is_padded(b"10"));
    }
}

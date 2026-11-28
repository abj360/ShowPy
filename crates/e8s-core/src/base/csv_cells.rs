//! csv_cells --- neutralizes spreadsheet formula cells in text tables
//!
//! Contains:
//!     FORMULA_TRIGGERS: lists the leading characters a spreadsheet treats as a formula
//!     FORMULA_GUARD: holds the prefix that turns a formula cell into text
//!     PLAIN_DECIMAL: states the plain decimal literal, which stays a number
//!     is_plain_decimal(): tells whether a whole cell is a plain decimal literal
//!     needs_guard(): tells whether a cell starts like a formula and is not a plain decimal

/// Lists the leading characters a spreadsheet treats as a formula.
pub const FORMULA_TRIGGERS: [&str; 6] = ["=", "+", "-", "@", "\t", "\r"];
/// Holds the prefix that turns a formula cell into text.
pub const FORMULA_GUARD: &str = "'";
/// States the plain decimal literal as an ASCII regular expression; such a cell stays a number.
pub const PLAIN_DECIMAL: &str = r"[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?";

fn digits(text: &[u8]) -> usize {
    text.iter().take_while(|byte| byte.is_ascii_digit()).count()
}

/// Tells whether a whole cell is a plain decimal literal: an optional sign, digits with an
/// optional point and fraction (or a point and digits), and an optional exponent.
#[must_use]
pub fn is_plain_decimal(cell: &[u8]) -> bool {
    let mut rest = cell
        .strip_prefix(b"+")
        .or_else(|| cell.strip_prefix(b"-"))
        .unwrap_or(cell);
    let whole = digits(rest);
    rest = &rest[whole..];
    if let Some(after_point) = rest.strip_prefix(b".") {
        let fraction = digits(after_point);
        if whole == 0 && fraction == 0 {
            return false;
        }
        rest = &after_point[fraction..];
    } else if whole == 0 {
        return false;
    }
    if let Some(exponent) = rest.strip_prefix(b"e").or_else(|| rest.strip_prefix(b"E")) {
        let unsigned = exponent
            .strip_prefix(b"+")
            .or_else(|| exponent.strip_prefix(b"-"))
            .unwrap_or(exponent);
        let power = digits(unsigned);
        return power > 0 && power == unsigned.len();
    }
    rest.is_empty()
}

/// Tells whether a cell starts like a formula and is not a plain decimal, so it needs the guard.
#[must_use]
pub fn needs_guard(cell: &[u8]) -> bool {
    FORMULA_TRIGGERS
        .iter()
        .any(|trigger| cell.starts_with(trigger.as_bytes()))
        && !is_plain_decimal(cell)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn formulas_need_the_guard_and_numbers_do_not() {
        for cell in [
            "=1+1", "@cue", "\tx", "\r", "-", "+.", "-1e", "--5", "+5\n", "-\u{661}",
        ] {
            assert!(needs_guard(cell.as_bytes()), "{cell:?}");
        }
        for cell in [
            "-5",
            "+3.5e10",
            "-.5",
            "-1e+5",
            "-1.e5",
            "-0005.000E-0003",
            "cue",
            "",
            " =1",
        ] {
            assert!(!needs_guard(cell.as_bytes()), "{cell:?}");
        }
    }
}

//! caps --- resource-safety caps on output sizes, checked before anything is allocated
//!
//! Contains:
//!     MAX_POINTS: bounds the points one call returns
//!     MAX_TABLE: bounds the samples in a curve lookup table
//!     MAX_GRID_CELLS: bounds the cells of a raster
//!     MAX_RANGE: bounds the IDs one range expands to
//!     MAX_DEPTH: bounds the nesting of a selection expression
//!     MAX_TEXT_CHARS: bounds the characters of one parser input
//!     MIN_LAYOUT_HEIGHT_M: sets the smallest cap height text layout accepts, in meters
//!     MAX_COORD_M: bounds every stored coordinate and length, in meters
//!     MAX_COUNTS: bounds every stored count
//!     MAX_EXTENSION_BYTES: bounds the UTF-8 JSON size of a show's `x-` extension values
//!     MIN_TEMPO: sets the slowest stored tempo, in counts per minute
//!     MAX_SPOTS: bounds the spots of one cast
//!     MAX_SETS: bounds the sets of one show
//!     size_error(): builds the error for a size that is negative or over its cap
//!     require_size(): rejects a requested size that is negative or over its cap
//!     size_beyond_i64(): builds the error for a size written as digits beyond 64 bits

use super::error::{ContextValue, CoreError, CoreResult};
use super::error_codes::{ErrorCode, ErrorKind};

/// Bounds the points one call returns.
pub const MAX_POINTS: i64 = 1_000_000;
/// Bounds the samples in a curve lookup table.
pub const MAX_TABLE: i64 = 1_000_000;
/// Bounds the cells of a raster.
pub const MAX_GRID_CELLS: i64 = 4_000_000;
/// Bounds the IDs one range expands to.
pub const MAX_RANGE: i64 = 10_000;
/// Bounds the nesting of a selection expression.
pub const MAX_DEPTH: i64 = 64;
/// Bounds the characters of one parser input.
pub const MAX_TEXT_CHARS: i64 = 1_000_000;
/// Sets the smallest cap height text layout accepts, in meters.
pub const MIN_LAYOUT_HEIGHT_M: f64 = 0.001;
/// Bounds every stored coordinate and length, in meters; real shows stay within a few hundred
/// meters, and a far larger value overflows geometry to NaN or infinity.
pub const MAX_COORD_M: f64 = 1e6;
/// Bounds every stored count; real shows stay under about 10,000 counts, and above 2**23 counts
/// one ulp exceeds the 1e-9-count clock tolerance.
pub const MAX_COUNTS: i64 = 1_000_000;
/// Sets the slowest stored tempo, in counts per minute; anything slower is not a performance
/// and drives the clock to infinity.
pub const MIN_TEMPO: f64 = 1e-3;
/// Bounds the spots of one cast; a large ensemble has a few hundred. A provisional bound well
/// past any real show; it promises no speed at the cap.
pub const MAX_SPOTS: i64 = 5_000;
/// Bounds the sets of one show; a long show has a few hundred. Provisional like `MAX_SPOTS`.
pub const MAX_SETS: i64 = 1_000;
/// Bounds the UTF-8 JSON size of a show's `x-` extension values, one mebibyte: host metadata is
/// a few settings or ids, and a larger value is a payload that belongs in an asset.
pub const MAX_EXTENSION_BYTES: i64 = 1_048_576;

/// Builds the message and hint for a size that is negative (`is_negative`) or over its cap.
///
/// The sizes are text, so a size beyond any integer type still reads exactly.
#[must_use]
pub fn size_error(requested: &str, cap: &str, is_negative: bool, what: &str) -> (String, String) {
    if is_negative {
        return (
            format!("{what} would need {requested}, which is negative"),
            format!("ask for zero or more {what}"),
        );
    }
    (
        format!("{what} would need {requested}, over the cap of {cap}"),
        format!("ask for fewer {what} or use a coarser setting"),
    )
}

/// Rejects a requested size that is negative or over its cap, before the caller allocates it.
///
/// # Errors
///
/// Returns a SchemaError carrying `what`, `requested` and `cap` for a size below 0 or over `cap`.
pub fn require_size(requested: i64, cap: i64, what: &str) -> CoreResult<()> {
    if (0..=cap).contains(&requested) {
        return Ok(());
    }
    let (message, hint) = size_error(
        &requested.to_string(),
        &cap.to_string(),
        requested < 0,
        what,
    );
    Err(
        CoreError::new(ErrorKind::Schema, ErrorCode::Schema, message, hint)
            .with_context("what", what)
            .with_context("requested", requested)
            .with_context("cap", cap),
    )
}

/// Builds the error for a requested size beyond 64 bits, written as decimal digits after an
/// optional `-`: a negative one, or one over every cap.
#[must_use]
pub fn size_beyond_i64(requested: &str, cap: i64, what: &str) -> CoreError {
    let (message, hint) = size_error(
        requested,
        &cap.to_string(),
        requested.starts_with('-'),
        what,
    );
    CoreError::new(ErrorKind::Schema, ErrorCode::Schema, message, hint)
        .with_context("what", what)
        .with_context("requested", ContextValue::Integer(requested.to_owned()))
        .with_context("cap", cap)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_size_over_its_cap_names_the_cap() {
        let Err(err) = require_size(1_000_001, MAX_POINTS, "points") else {
            unreachable!("an oversized request passed");
        };
        assert_eq!(
            err.message(),
            "points would need 1000001, over the cap of 1000000"
        );
        assert_eq!(err.hint(), "ask for fewer points or use a coarser setting");
    }

    #[test]
    fn a_negative_size_is_rejected_even_under_a_negative_cap() {
        let Err(err) = require_size(-1, MAX_POINTS, "points") else {
            unreachable!("a negative request passed");
        };
        assert_eq!(err.message(), "points would need -1, which is negative");
        assert_eq!(require_size(0, MAX_POINTS, "points"), Ok(()));
        assert_eq!(require_size(MAX_POINTS, MAX_POINTS, "points"), Ok(()));
    }

    #[test]
    fn a_size_beyond_64_bits_keeps_every_digit() {
        let err = size_beyond_i64("100000000000000000000000", MAX_RANGE, "range IDs");
        assert_eq!(
            err.message(),
            "range IDs would need 100000000000000000000000, over the cap of 10000"
        );
        assert_eq!(
            err.context()[1].1,
            ContextValue::Integer("100000000000000000000000".to_owned())
        );
    }
}

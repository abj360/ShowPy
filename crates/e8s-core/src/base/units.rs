//! units --- angle helpers for facings: shortest turns and facing vectors
//!
//! Contains:
//!     ZERO_VECTOR_TOL: the length under which a direction has no facing
//!     angle_diff(): returns the signed shortest turn from a to b
//!     facing_vector(): converts a facing to a unit direction
//!     vector_facing(): converts a direction to a facing

use std::f64::consts::PI;

use super::error::{CoreError, CoreResult, require_finite};
use super::error_codes::{ErrorCode, ErrorKind};
use super::numbers::{TURN_DEG, normalize_angle, py_float_repr};

/// The length under which a direction has no facing.
pub const ZERO_VECTOR_TOL: f64 = 1e-12;
const HALF_TURN_DEG: f64 = 180.0;

/// Returns the signed shortest turn from `a` to `b`, in (-180, 180].
///
/// # Errors
///
/// Returns a SchemaError when `b - a` is NaN or infinite.
pub fn angle_diff(a: f64, b: f64) -> CoreResult<f64> {
    let turn = normalize_angle(b - a)?;
    Ok(if turn > HALF_TURN_DEG {
        turn - TURN_DEG
    } else {
        turn
    })
}

/// Converts a facing to a unit direction; 0 degrees faces the audience, (0, -1).
///
/// # Errors
///
/// Returns a SchemaError when `theta` is NaN or infinite.
pub fn facing_vector(theta: f64) -> CoreResult<(f64, f64)> {
    let theta = require_finite("theta", theta)?;
    let radians = theta * (PI / HALF_TURN_DEG);
    Ok((libm::sin(radians) + 0.0, -libm::cos(radians) + 0.0))
}

/// Converts a direction to a facing in [0, 360).
///
/// # Errors
///
/// Returns a ZeroLengthError for a direction shorter than `ZERO_VECTOR_TOL`, and a SchemaError
/// when the direction has a NaN part.
pub fn vector_facing(x: f64, y: f64) -> CoreResult<f64> {
    if libm::hypot(x, y) < ZERO_VECTOR_TOL {
        return Err(CoreError::new(
            ErrorKind::ZeroLength,
            ErrorCode::ZeroLength,
            "facing of a zero vector is undefined",
            "pass a non-zero direction or keep the previous facing",
        )
        .with_context(
            "vector",
            format!("({}, {})", py_float_repr(x), py_float_repr(y)),
        ));
    }
    normalize_angle(libm::atan2(x, -y) * (HALF_TURN_DEG / PI))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn angle_diff_is_the_signed_shortest_turn() {
        assert_eq!(angle_diff(350.0, 10.0), Ok(20.0));
        assert_eq!(angle_diff(10.0, 350.0), Ok(-20.0));
        assert_eq!(angle_diff(0.0, 180.0), Ok(180.0));
        assert!(angle_diff(f64::NAN, 1.0).is_err());
    }

    #[test]
    fn facing_zero_points_at_the_audience() {
        assert_eq!(facing_vector(0.0), Ok((0.0, -1.0)));
        assert!(facing_vector(f64::INFINITY).is_err());
    }

    #[test]
    fn vector_facing_reads_directions_and_refuses_zero() {
        assert_eq!(vector_facing(1.0, 0.0), Ok(90.0));
        assert_eq!(vector_facing(0.0, -1.0), Ok(0.0));
        let Err(err) = vector_facing(0.0, 0.0) else {
            unreachable!("a zero vector had a facing");
        };
        assert_eq!(err.kind(), ErrorKind::ZeroLength);
    }
}

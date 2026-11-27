//! base --- units, identifiers, errors and canonical encodings of the `e8s.core` package
//!
//! Contains:
//!     caps: resource-safety caps on output sizes
//!     error: the error every fallible core function returns
//!     error_codes: the stable error codes and exception classes, generated from Python
//!     ids: the ID rules and checks
//!     numbers: plain-decimal number text and angle wrapping
//!     refs: reference and transition ID parsing and formatting
//!     rounding: rounding to a step in decimal arithmetic
//!     text: the Unicode behavior of Python strings that ID checks depend on
//!     units: angle helpers for facings

pub mod caps;
pub mod error;
pub mod error_codes;
pub mod ids;
pub mod numbers;
pub mod refs;
pub mod rounding;
pub mod text;
pub mod units;

pub use error::{ContextValue, CoreError, CoreResult, require_finite};
pub use error_codes::{ErrorCode, ErrorKind};

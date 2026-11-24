//! base --- units, identifiers, errors and canonical encodings of the `e8s.core` package

pub mod error;
pub mod error_codes;

pub use error::{ContextValue, CoreError, CoreResult, require_finite};
pub use error_codes::{ErrorCode, ErrorKind};

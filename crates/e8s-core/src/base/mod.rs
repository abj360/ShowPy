//! base --- units, identifiers, errors and canonical encodings of the `e8s.core` package
//!
//! Contains:
//!     canonical: the JSON data model and its RFC 8785 canonical bytes
//!     caps: resource-safety caps on output sizes
//!     error: the error every fallible core function returns
//!     error_codes: the stable error codes and exception classes, generated from Python
//!     hashing: SHA-256 content hashes under a domain tag, and asset URIs
//!     ids: the ID rules and checks
//!     json_scan: reads JSON text with the grammar and number rules of CPython's `json`
//!     numbers: plain-decimal number text and angle wrapping
//!     refs: reference and transition ID parsing and formatting
//!     rounding: rounding to a step in decimal arithmetic
//!     strict_json: parses stored and imported JSON strictly
//!     text: the Unicode behavior of Python strings that ID checks depend on
//!     units: angle helpers for facings

pub mod canonical;
pub mod caps;
pub mod error;
pub mod error_codes;
pub mod hashing;
pub mod ids;
pub mod json_scan;
pub mod numbers;
pub mod refs;
pub mod rounding;
pub mod strict_json;
pub mod text;
pub mod units;

pub use error::{ContextValue, CoreError, CoreResult, require_finite};
pub use error_codes::{ErrorCode, ErrorKind};

//! base --- units, identifiers, errors and canonical encodings of the `e8s.core` package
//!
//! Contains:
//!     canonical: the JSON data model and its RFC 8785 canonical bytes
//!     caps: resource-safety caps on output sizes
//!     csv_cells: neutralizes spreadsheet formula cells in text tables
//!     decimal: non-negative integers held as ASCII digits
//!     error: the error every fallible core function returns
//!     error_codes: the stable error codes and exception classes, generated from Python
//!     hashing: SHA-256 content hashes under a domain tag, and asset URIs
//!     ids: the ID rules and checks
//!     json_scan: reads JSON text with the grammar and number rules of CPython's `json`
//!     natural: natural order of IDs, numbers by value
//!     numbers: plain-decimal number text and angle wrapping
//!     ranges: ID ranges, an ID's trailing number and run compaction
//!     refs: reference and transition ID parsing and formatting
//!     rounding: rounding to a step in decimal arithmetic
//!     similar: close-name suggestions scored as difflib scores them
//!     strict_json: parses stored and imported JSON strictly
//!     text: the Unicode behavior of Python strings that ID checks depend on
//!     text_bytes: Python text as bytes that may hold lone surrogates
//!     units: angle helpers for facings
//!     util: noise ranks, float comparison and count chunks

pub mod canonical;
pub mod caps;
pub mod csv_cells;
pub mod decimal;
pub mod error;
pub mod error_codes;
pub mod hashing;
pub mod ids;
pub mod json_scan;
pub mod natural;
pub mod numbers;
pub mod ranges;
pub mod refs;
pub mod rounding;
pub mod similar;
pub mod strict_json;
pub mod text;
pub mod text_bytes;
pub mod units;
pub mod util;

pub use error::{ContextValue, CoreError, CoreResult, require_finite};
pub use error_codes::{ErrorCode, ErrorKind};

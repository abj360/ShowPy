//! error --- the error every fallible core function returns, carrying what an e8s exception carries
//!
//! Contains:
//!     ContextValue: holds one JSON value of an error's debugging context
//!     CoreError: reports a core failure with its exception class, code, message, context, hint
//!     CoreResult: the result of every fallible core function
//!     require_finite(): rejects a NaN or infinite input number with a SchemaError

use std::borrow::Cow;
use std::fmt;

use super::error_codes::{ErrorCode, ErrorKind};

/// Holds one JSON value of an error's debugging context.
#[derive(Clone, Debug, PartialEq)]
pub enum ContextValue {
    /// A JSON boolean.
    Bool(bool),
    /// A JSON integer.
    Int(i64),
    /// A JSON integer beyond 64 bits, as its decimal digits after an optional `-`.
    Integer(String),
    /// A JSON number; the binding shows a non-finite one as text, as Python's context does.
    Float(f64),
    /// A JSON string.
    Text(String),
}

impl From<bool> for ContextValue {
    fn from(value: bool) -> Self {
        Self::Bool(value)
    }
}

impl From<i64> for ContextValue {
    fn from(value: i64) -> Self {
        Self::Int(value)
    }
}

impl From<f64> for ContextValue {
    fn from(value: f64) -> Self {
        Self::Float(value)
    }
}

impl From<&str> for ContextValue {
    fn from(value: &str) -> Self {
        Self::Text(value.to_owned())
    }
}

impl From<String> for ContextValue {
    fn from(value: String) -> Self {
        Self::Text(value)
    }
}

/// Reports a core failure with the fields the binding raises it with.
///
/// The binding raises the class `kind` names in `e8s.core.errors`, with the same code, message,
/// context and hint, so a caller cannot tell a core error from one Python raised.
#[derive(Clone, Debug, PartialEq)]
pub struct CoreError {
    kind: ErrorKind,
    code: ErrorCode,
    message: String,
    context: Vec<(Cow<'static, str>, ContextValue)>,
    hint: Cow<'static, str>,
}

/// The result of every fallible core function.
pub type CoreResult<T> = Result<T, CoreError>;

impl CoreError {
    /// Creates an error of one exception class and code, with an empty context.
    ///
    /// A raise site names the class and the code together (`ErrorKind::Schema,
    /// ErrorCode::Schema`); the error-code generator reads the raising class from that pair.
    #[must_use]
    pub fn new(
        kind: ErrorKind,
        code: ErrorCode,
        message: impl Into<String>,
        hint: impl Into<Cow<'static, str>>,
    ) -> Self {
        Self {
            kind,
            code,
            message: message.into(),
            context: Vec::new(),
            hint: hint.into(),
        }
    }

    /// Returns the error with one more context entry, kept in insertion order.
    #[must_use]
    pub fn with_context(
        mut self,
        key: impl Into<Cow<'static, str>>,
        value: impl Into<ContextValue>,
    ) -> Self {
        self.context.push((key.into(), value.into()));
        self
    }

    /// Returns the exception class the binding raises.
    #[must_use]
    pub const fn kind(&self) -> ErrorKind {
        self.kind
    }

    /// Returns the stable error code.
    #[must_use]
    pub const fn code(&self) -> ErrorCode {
        self.code
    }

    /// Returns what went wrong.
    #[must_use]
    pub fn message(&self) -> &str {
        &self.message
    }

    /// Returns the debugging context, in insertion order.
    #[must_use]
    pub fn context(&self) -> &[(Cow<'static, str>, ContextValue)] {
        &self.context
    }

    /// Returns what the caller is allowed to do instead.
    #[must_use]
    pub fn hint(&self) -> &str {
        &self.hint
    }
}

impl fmt::Display for CoreError {
    /// Formats the error as Python's `E8sError.__str__` does: `CODE: message (hint: hint)`.
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "{}: {} (hint: {})",
            self.code.as_str(),
            self.message,
            self.hint
        )
    }
}

impl std::error::Error for CoreError {}

/// Rejects a NaN or infinite input number with a SchemaError, as the Python models do.
///
/// # Errors
///
/// Returns a `SchemaError` with code `SCHEMA` naming `field` and the rejected value.
pub fn require_finite(field: &str, value: f64) -> CoreResult<f64> {
    if value.is_finite() {
        return Ok(value);
    }
    Err(CoreError::new(
        ErrorKind::Schema,
        ErrorCode::Schema,
        format!("{field} must be a finite number, got {value}"),
        "pass a finite number",
    )
    .with_context("field", field.to_owned())
    .with_context("value", value))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn display_matches_the_python_error_text() {
        let err = CoreError::new(
            ErrorKind::Timeline,
            ErrorCode::Timeline,
            "no tempo",
            "add one",
        );
        assert_eq!(err.to_string(), "TIMELINE: no tempo (hint: add one)");
    }

    #[test]
    fn context_keeps_insertion_order() {
        let err = CoreError::new(ErrorKind::E8s, ErrorCode::Stage, "m", "h")
            .with_context("b", 1_i64)
            .with_context("a", "x");
        let keys: Vec<&str> = err.context().iter().map(|(key, _)| key.as_ref()).collect();
        assert_eq!(keys, ["b", "a"]);
    }

    #[test]
    fn require_finite_passes_finite_numbers_through() {
        assert_eq!(require_finite("x", -0.5), Ok(-0.5));
        assert_eq!(require_finite("x", f64::MAX), Ok(f64::MAX));
    }

    #[test]
    fn require_finite_rejects_nan_and_infinities_as_schema_errors() {
        for value in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
            let Err(err) = require_finite("x", value) else {
                unreachable!("{value} passed the finite check");
            };
            assert_eq!(err.kind(), ErrorKind::Schema);
            assert_eq!(err.code(), ErrorCode::Schema);
            assert_eq!(
                err.context()[0],
                (Cow::Borrowed("field"), ContextValue::from("x"))
            );
        }
    }
}

//! strict_json --- parses stored and imported JSON strictly, so two readers never disagree on it
//!
//! Standard JSON has no NaN or infinity, and a document with a repeated key or a lone surrogate
//! reads differently in different parsers; a byte-order mark hides the UTF-8 encoding. All four
//! are refused, each with SchemaError code STRICT_JSON naming the document. A number too large
//! for a float, such as 1e400, parses to infinity and is refused when the model validates, not
//! here.
//!
//! Contains:
//!     UTF8_BOM: the byte-order mark a document may not start with
//!     STRICT_HINT, SURROGATE_HINT: the hints of the STRICT_JSON errors
//!     Invalid: says where text stopped being JSON, and why
//!     ParseFailure: the ways parse_strict_json refuses a document
//!     json_nesting(): measures how deep arrays and objects nest in JSON bytes, without recursing
//!     require_nesting(): refuses JSON bytes nested deeper than MAX_NESTING levels
//!     parse_strict_json(): parses UTF-8 JSON bytes, refusing non-standard tokens, repeated keys,
//!         a byte-order mark and lone surrogates
//!     invalid_json(): builds the error for bytes that are not valid JSON
//!     strict_error(): builds a STRICT_JSON error naming the document and the problem
//!     copy_refused(): builds the STRICT_JSON error for a value that is not JSON
//!     lone_surrogate_error(): builds the STRICT_JSON error for a lone surrogate
//!     repeated_key_error(): builds the STRICT_JSON error for a repeated key

use super::canonical::{Json, MAX_INT_DIGITS, MAX_NESTING};
use super::error::{ContextValue, CoreError};
use super::error_codes::{ErrorCode, ErrorKind};
use super::json_scan::Scanner;
use super::text::{TextHost, str_repr};

/// The byte-order mark a document may not start with.
pub const UTF8_BOM: &[u8] = b"\xef\xbb\xbf";
/// The hint of a STRICT_JSON error.
pub const STRICT_HINT: &str = "write standard UTF-8 JSON, as save_show() and export_json() do";
/// The hint of the STRICT_JSON error for a lone surrogate.
pub const SURROGATE_HINT: &str =
    "write text as valid Unicode; a \\ud800-\\udfff escape must be part of a pair";
const QUOTE: u8 = b'"';
const BACKSLASH: u8 = b'\\';
const LONE_SURROGATE: &str = "a lone surrogate";
/// Marks the bytes json_nesting reads: brackets, the quote and the backslash.
const STRUCTURE: [bool; 256] = {
    let mut marks = [false; 256];
    let mut index = 0;
    let bytes = b"[]{}\"\\";
    while index < bytes.len() {
        marks[bytes[index] as usize] = true;
        index += 1;
    }
    marks
};

/// Says where text stopped being JSON, and why; the binding asks Python for the exact words.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Invalid {
    /// The bytes are not UTF-8; the offset is the first byte that is not.
    Utf8(usize),
    /// The text breaks the JSON grammar at this byte offset.
    Syntax(usize),
    /// The int at this byte offset has more digits than the interpreter converts.
    IntDigits(usize),
}

impl Invalid {
    /// Describes the problem in the core's own words, for callers with no Python.
    #[must_use]
    pub fn describe(self) -> String {
        match self {
            Self::Utf8(at) => format!("bytes are not UTF-8 at byte {at}"),
            Self::Syntax(at) => format!("syntax error at byte {at}"),
            Self::IntDigits(at) => format!("int at byte {at} has too many digits"),
        }
    }
}

/// The ways parse_strict_json refuses a document.
#[derive(Clone, Debug, PartialEq)]
pub enum ParseFailure {
    /// A fully described refusal.
    Refused(CoreError),
    /// The text is not JSON; `invalid_json` describes it.
    Invalid(Invalid),
    /// A key repeats; its exact text, as generalized UTF-8 when it holds a lone surrogate.
    RepeatedKey(Vec<u8>),
    /// A string or key holds a lone surrogate; `lone_surrogate_error` describes it.
    LoneSurrogate,
}

impl From<CoreError> for ParseFailure {
    fn from(error: CoreError) -> Self {
        Self::Refused(error)
    }
}

/// Measures how deep arrays and objects nest in JSON bytes, counting brackets outside strings.
///
/// Escaped backslashes and then escaped quotes are dropped first, so every quote left opens or
/// closes a string; brackets between a quote pair are skipped. For text that is not JSON the
/// count is only an estimate; the parser refuses such text anyway.
///
/// The drops behave as two whole-text replacements, `\\` first and `\"` second: a run of
/// backslashes loses its pairs from the left, so a quote is dropped exactly when an odd run
/// of backslashes comes right before it. One pass reads that without copying the text.
///
/// Returns:
/// depth: The most arrays and objects open at once; 0 for a bare scalar.
#[must_use]
pub fn json_nesting(raw: &[u8]) -> usize {
    let mut depth: i64 = 0;
    let mut deepest: i64 = 0;
    let mut in_string = false;
    let mut backslashes: usize = 0;
    for &byte in raw {
        if !STRUCTURE[usize::from(byte)] {
            backslashes = 0;
            continue;
        }
        if byte == BACKSLASH {
            backslashes += 1;
            continue;
        }
        let escaped = backslashes % 2 == 1;
        backslashes = 0;
        match byte {
            QUOTE if !escaped => in_string = !in_string,
            b'[' | b'{' if !in_string => {
                depth += 1;
                deepest = deepest.max(depth);
            }
            b']' | b'}' if !in_string => depth -= 1,
            _ => {}
        }
    }
    usize::try_from(deepest).unwrap_or(0)
}

/// Refuses JSON bytes whose arrays and objects nest deeper than MAX_NESTING levels.
///
/// # Errors
///
/// Returns a SchemaError naming the document and the depth found.
pub fn require_nesting(raw: &[u8], document: &str) -> Result<(), CoreError> {
    let depth = json_nesting(raw);
    if depth <= MAX_NESTING {
        return Ok(());
    }
    Err(CoreError::new(
        ErrorKind::Schema,
        ErrorCode::Schema,
        format!("{document} nests deeper than {MAX_NESTING} levels"),
        format!("write JSON nested at most {MAX_NESTING} levels"),
    )
    .with_context("path", document)
    .with_context("depth", count(depth))
    .with_context("max_nesting", count(MAX_NESTING)))
}

fn count(value: usize) -> i64 {
    i64::try_from(value).unwrap_or(i64::MAX)
}

/// Parses UTF-8 JSON bytes, refusing what standard JSON forbids or leaves ambiguous.
///
/// Checks run in this order: a leading byte-order mark, the nesting cap, UTF-8, then the
/// grammar from left to right, where a NaN or infinity token, an int longer than
/// `int_digit_limit` digits (0 for no limit) and an object's repeated key (once the object
/// closes) refuse the text where they are read; a lone surrogate is refused last.
///
/// # Errors
///
/// Returns the first refusal: STRICT_JSON errors for the byte-order mark, a non-standard token
/// and a repeated key; a SchemaError for the nesting cap; an `Invalid` for text that is not
/// JSON; `LoneSurrogate` last.
pub fn parse_strict_json(
    raw: &[u8],
    document: &str,
    int_digit_limit: usize,
    host: &dyn TextHost,
) -> Result<Json, ParseFailure> {
    if raw.starts_with(UTF8_BOM) {
        return Err(strict_error(document, "starts with a byte-order mark")
            .with_context("position", 0_i64)
            .into());
    }
    require_nesting(raw, document)?;
    let text = std::str::from_utf8(raw)
        .map_err(|error| ParseFailure::Invalid(Invalid::Utf8(error.valid_up_to())))?;
    let mut scanner = Scanner::new(text, document, int_digit_limit);
    let value = match scanner.document() {
        Ok(value) => value,
        Err(ParseFailure::RepeatedKey(key)) => return Err(repeated(document, key, host)),
        Err(failure) => return Err(failure),
    };
    if scanner.saw_lone_surrogate {
        return Err(ParseFailure::LoneSurrogate);
    }
    Ok(value)
}

fn repeated(document: &str, key: Vec<u8>, host: &dyn TextHost) -> ParseFailure {
    match String::from_utf8(key) {
        Ok(text) => match str_repr(host, &text) {
            Ok(shown) => repeated_key_error(document, &shown, &text).into(),
            Err(error) => error.into(),
        },
        Err(exact) => ParseFailure::RepeatedKey(exact.into_bytes()),
    }
}

/// Builds the error for bytes that are not valid JSON, with `detail` saying why.
#[must_use]
pub fn invalid_json(document: &str, detail: &str) -> CoreError {
    CoreError::new(
        ErrorKind::Schema,
        ErrorCode::Schema,
        format!("{document} is not valid JSON: {detail}"),
        format!(
            "write UTF-8 JSON nested at most {MAX_NESTING} levels, ints of at most \
             {MAX_INT_DIGITS} digits, as save_show() does"
        ),
    )
    .with_context("path", document)
}

/// Builds a STRICT_JSON error naming the document and the problem.
#[must_use]
pub fn strict_error(document: &str, problem: &str) -> CoreError {
    CoreError::new(
        ErrorKind::Schema,
        ErrorCode::StrictJson,
        format!("{document} is not strict JSON: {problem}"),
        STRICT_HINT,
    )
    .with_context("path", document)
    .with_context("problem", problem)
}

/// Builds the STRICT_JSON error for a value `json.dumps` refused, with its exception's text
/// and class name.
#[must_use]
pub fn copy_refused(document: &str, detail: &str, kind: &str) -> CoreError {
    CoreError::new(
        ErrorKind::Schema,
        ErrorCode::StrictJson,
        format!("{document} is not strict JSON: {detail}"),
        STRICT_HINT,
    )
    .with_context("path", document)
    .with_context("problem", kind)
}

/// Builds the STRICT_JSON error for a lone surrogate.
#[must_use]
pub fn lone_surrogate_error(document: &str) -> CoreError {
    CoreError::new(
        ErrorKind::Schema,
        ErrorCode::StrictJson,
        format!("{document} is not strict JSON: {LONE_SURROGATE}"),
        SURROGATE_HINT,
    )
    .with_context("path", document)
    .with_context("problem", LONE_SURROGATE)
}

/// Builds the STRICT_JSON error for a repeated key, shown as Python's `repr` writes it.
#[must_use]
pub fn repeated_key_error(document: &str, shown: &str, key: &str) -> CoreError {
    strict_error(document, &format!("repeated key {shown}"))
        .with_context("key", ContextValue::from(key))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::base::text::StdText;

    fn parse(raw: &[u8]) -> Result<Json, ParseFailure> {
        parse_strict_json(raw, "d.json", 4300, &StdText)
    }

    fn code_of(raw: &[u8]) -> Option<ErrorCode> {
        match parse(raw) {
            Err(ParseFailure::Refused(error)) => Some(error.code()),
            _ => None,
        }
    }

    #[test]
    fn nesting_counts_only_brackets_outside_strings() {
        assert_eq!(json_nesting(b"1"), 0);
        assert_eq!(json_nesting(b"{\"a\": [1, {\"b\": []}]}"), 4);
        assert_eq!(json_nesting(b"[\"[[[[{{{\", \"]]]]\"]"), 1);
        assert_eq!(json_nesting(b"[\"\\\"]"), 1);
        assert_eq!(json_nesting(b"[\"a\\\"[[[\", [[]]]"), 3);
        assert_eq!(json_nesting(b"]]][[["), 0);
        assert_eq!(json_nesting(b"[\"\\\\\"[[\"]"), 3);
    }

    #[test]
    fn standard_json_parses() {
        let value = parse(b" {\"a\": [1, 2.5, -0.0, null, true], \"b\": \"\\ud83d\\ude00\"} ");
        let expected = Json::Object(vec![
            (
                "a".to_owned(),
                Json::Array(vec![
                    Json::Int(1),
                    Json::Float(2.5),
                    Json::Float(-0.0),
                    Json::Null,
                    Json::Bool(true),
                ]),
            ),
            ("b".to_owned(), Json::Text("\u{1f600}".to_owned())),
        ]);
        assert_eq!(value, Ok(expected));
    }

    #[test]
    fn refusals_carry_their_codes() {
        assert_eq!(code_of(b"\xef\xbb\xbf{}"), Some(ErrorCode::StrictJson));
        assert_eq!(code_of(b"[NaN]"), Some(ErrorCode::StrictJson));
        assert_eq!(code_of(b"[-Infinity]"), Some(ErrorCode::StrictJson));
        assert_eq!(
            code_of(b"{\"a\": 1, \"a\": 2}"),
            Some(ErrorCode::StrictJson)
        );
        assert_eq!(parse(b"[\"\\ud800\"]"), Err(ParseFailure::LoneSurrogate));
        assert_eq!(code_of(&[b'['; 101]), Some(ErrorCode::Schema));
    }

    #[test]
    fn invalid_text_names_the_first_offending_byte() {
        assert_eq!(
            parse(b"[1,]"),
            Err(ParseFailure::Invalid(Invalid::Syntax(3)))
        );
        assert_eq!(
            parse(b"[01]"),
            Err(ParseFailure::Invalid(Invalid::Syntax(2)))
        );
        assert_eq!(
            parse(b"\"\xff\""),
            Err(ParseFailure::Invalid(Invalid::Utf8(1)))
        );
        let long = format!("[{}]", "1".repeat(4301));
        assert_eq!(
            parse(long.as_bytes()),
            Err(ParseFailure::Invalid(Invalid::IntDigits(1)))
        );
    }

    #[test]
    fn the_first_refusal_in_reading_order_wins() {
        assert_eq!(
            parse(b"[1,, NaN]"),
            Err(ParseFailure::Invalid(Invalid::Syntax(3)))
        );
        assert_eq!(code_of(b"[NaN,,]"), Some(ErrorCode::StrictJson));
        assert_eq!(
            code_of(b"[\"\\ud800\", {\"a\": 1, \"a\": 1}]"),
            Some(ErrorCode::StrictJson)
        );
        assert!(matches!(
            parse(b"[\"\\ud800\", 1,]"),
            Err(ParseFailure::Invalid(Invalid::Syntax(_)))
        ));
    }

    #[test]
    fn repeated_surrogate_keys_keep_their_exact_text() {
        assert_eq!(
            parse(b"{\"\\ud800\": 1, \"\\ud800\": 2}"),
            Err(ParseFailure::RepeatedKey(vec![0xed, 0xa0, 0x80]))
        );
        assert_eq!(
            parse(b"{\"\\ud800\": 1, \"\\ud801\": 2}"),
            Err(ParseFailure::LoneSurrogate)
        );
    }
}

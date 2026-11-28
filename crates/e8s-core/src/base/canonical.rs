//! canonical --- the JSON data model and its RFC 8785 canonical bytes
//!
//! Contains:
//!     Json: one value of the JSON data model, with ints kept exact up to 640 digits
//!     TAG_PREFIX, MAP_TAG: the reserved key prefix and the tag of a mapping with non-text keys
//!     MAX_NESTING: the deepest container nesting a canonical value may hold
//!     MAX_INT_DIGITS: the most digits a canonical int may have
//!     CONTEXT_TEXT_CHARS: caps the characters of text an error repeats
//!     write(): appends the RFC 8785 bytes of a value
//!     encoded(): returns the RFC 8785 bytes of a value
//!     es_number(): writes a finite float as ECMAScript Number::toString does
//!     sorted_members(): sorts set members by their canonical bytes
//!     tagged_map(): builds the tagged form of a mapping that has a key that is not text
//!     checked_float(): folds -0.0 into 0.0 and rejects NaN and infinity
//!     nesting_error(), int_too_long(), lone_surrogate(), reserved_key(), unsupported_type(),
//!         unsupported_field(), model_dump_failed(): the errors canonical_value raises

use std::cmp::Ordering;

use super::error::{CoreError, CoreResult};
use super::error_codes::{ErrorCode, ErrorKind};
use super::numbers::{float_digits, py_float_repr};

/// The key prefix that marks type tags; no mapping key may start with it.
pub const TAG_PREFIX: &str = "$showpy:";
/// The single key of the tagged form of a mapping that has a key that is not text.
pub const MAP_TAG: &str = "$showpy:map";
/// The deepest container nesting a canonical value may hold.
///
/// One input level can become three output levels (tagged map, pair list, pair) and the Python
/// encoder recursed once per output level, so 100 kept it under CPython's default recursion
/// limit of 1000.
pub const MAX_NESTING: usize = 100;
/// The most digits a canonical int may have.
///
/// CPython refuses to print ints longer than `sys.int_max_str_digits`, which the environment
/// can lower to 640 and no further; this cap gives every environment the same bytes or the
/// same error.
pub const MAX_INT_DIGITS: usize = 640;
/// Caps the characters of text an error repeats.
pub const CONTEXT_TEXT_CHARS: usize = 80;
const ES_MAX_PLAIN_POINT: i64 = 21;
const ES_MIN_PLAIN_POINT: i64 = -6;
const SUPPORTED_HINT: &str = "pass None, bool, int, float, str, lists, tuples, sets, mappings, \
                              pydantic models or numpy values";
const HEX_DIGITS: &[u8; 16] = b"0123456789abcdef";

/// The canonical bytes of a tagged pair's key and value, its sort key.
type PairBytes = (Vec<u8>, Vec<u8>);

/// One value of the JSON data model.
///
/// Object members keep their insertion order; the writer sorts them. A `BigInt` holds the
/// decimal text of an int outside the `i64` range, sign included.
#[derive(Clone, Debug, PartialEq)]
pub enum Json {
    /// JSON `null`.
    Null,
    /// A JSON boolean.
    Bool(bool),
    /// An int within the `i64` range.
    Int(i64),
    /// The decimal text of an int outside the `i64` range.
    BigInt(String),
    /// A finite float.
    Float(f64),
    /// A JSON string.
    Text(String),
    /// A JSON array.
    Array(Vec<Json>),
    /// A JSON object, members in insertion order.
    Object(Vec<(String, Json)>),
}

/// Returns the RFC 8785 bytes of a value.
///
/// Keys are sorted by UTF-16 code units, strings carry only the escapes RFC 8785 requires and
/// floats use the ECMAScript shortest round-trip form.
#[must_use]
pub fn encoded(value: &Json) -> Vec<u8> {
    let mut out = Vec::new();
    write(value, &mut out);
    out
}

/// Appends the RFC 8785 bytes of a value to `out`.
pub fn write(value: &Json, out: &mut Vec<u8>) {
    match value {
        Json::Null => out.extend_from_slice(b"null"),
        Json::Bool(true) => out.extend_from_slice(b"true"),
        Json::Bool(false) => out.extend_from_slice(b"false"),
        Json::Int(number) => out.extend_from_slice(number.to_string().as_bytes()),
        Json::BigInt(text) => out.extend_from_slice(text.as_bytes()),
        Json::Float(number) => out.extend_from_slice(es_number(*number).as_bytes()),
        Json::Text(text) => quote(text, out),
        Json::Array(items) => {
            out.push(b'[');
            for (index, item) in items.iter().enumerate() {
                if index > 0 {
                    out.push(b',');
                }
                write(item, out);
            }
            out.push(b']');
        }
        Json::Object(members) => write_object(members, out),
    }
}

/// Appends an object with its keys sorted by UTF-16 code units, as RFC 8785 requires.
fn write_object(members: &[(String, Json)], out: &mut Vec<u8>) {
    let mut order: Vec<&(String, Json)> = members.iter().collect();
    order.sort_by(|left, right| utf16_order(&left.0, &right.0));
    out.push(b'{');
    for (index, (key, value)) in order.into_iter().enumerate() {
        if index > 0 {
            out.push(b',');
        }
        quote(key, out);
        out.push(b':');
        write(value, out);
    }
    out.push(b'}');
}

fn utf16_order(left: &str, right: &str) -> Ordering {
    if left.is_ascii() && right.is_ascii() {
        return left.cmp(right);
    }
    left.encode_utf16().cmp(right.encode_utf16())
}

/// Quotes text with the minimal escapes of RFC 8785: quote, backslash and controls only.
fn quote(text: &str, out: &mut Vec<u8>) {
    out.push(b'"');
    for &byte in text.as_bytes() {
        match byte {
            b'"' => out.extend_from_slice(b"\\\""),
            b'\\' => out.extend_from_slice(b"\\\\"),
            0x08 => out.extend_from_slice(b"\\b"),
            0x0c => out.extend_from_slice(b"\\f"),
            b'\n' => out.extend_from_slice(b"\\n"),
            b'\r' => out.extend_from_slice(b"\\r"),
            b'\t' => out.extend_from_slice(b"\\t"),
            0x00..=0x1f => {
                out.extend_from_slice(b"\\u00");
                out.push(HEX_DIGITS[usize::from(byte >> 4)]);
                out.push(HEX_DIGITS[usize::from(byte & 0x0f)]);
            }
            _ => out.push(byte),
        }
    }
    out.push(b'"');
}

/// Writes a finite float as ECMAScript Number::toString does (ECMA-262 Number::toString).
///
/// Zero of either sign writes `0`; NaN and infinity never reach the writer.
#[must_use]
pub fn es_number(number: f64) -> String {
    if number == 0.0 {
        return "0".to_owned();
    }
    if number < 0.0 {
        return format!("-{}", es_number(-number));
    }
    let (digits, point) = float_digits(number);
    place_point(&digits, point)
}

/// Writes `0.digits * 10**point` in plain or exponent form by the ECMAScript rules.
fn place_point(digits: &str, point: i64) -> String {
    let count = i64::try_from(digits.len()).unwrap_or(i64::MAX);
    let zeros = |n: i64| "0".repeat(usize::try_from(n).unwrap_or(0));
    if count <= point && point <= ES_MAX_PLAIN_POINT {
        return format!("{digits}{}", zeros(point - count));
    }
    if 0 < point && point <= ES_MAX_PLAIN_POINT {
        let (whole, fraction) = digits.split_at(usize::try_from(point).unwrap_or(0));
        return format!("{whole}.{fraction}");
    }
    if ES_MIN_PLAIN_POINT < point && point <= 0 {
        return format!("0.{}{digits}", zeros(-point));
    }
    let exponent = point - 1;
    let sign = if exponent >= 0 { '+' } else { '-' };
    let (first, rest) = digits.split_at(1.min(digits.len()));
    if rest.is_empty() {
        format!("{first}e{sign}{}", exponent.abs())
    } else {
        format!("{first}.{rest}e{sign}{}", exponent.abs())
    }
}

/// Sorts set members by their canonical bytes, keeping the order of equal ones.
#[must_use]
pub fn sorted_members(members: Vec<Json>) -> Vec<Json> {
    let mut keyed: Vec<(Vec<u8>, Json)> = members
        .into_iter()
        .map(|member| (encoded(&member), member))
        .collect();
    keyed.sort_by(|left, right| left.0.cmp(&right.0));
    keyed.into_iter().map(|(_, member)| member).collect()
}

/// Builds `{"$showpy:map": [[key, value], ...]}` with the pairs sorted by the canonical bytes
/// of each key, then of each value, keeping the order of equal pairs.
#[must_use]
pub fn tagged_map(pairs: Vec<(Json, Json)>) -> Json {
    let mut keyed: Vec<(PairBytes, Json)> = pairs
        .into_iter()
        .map(|(key, value)| {
            (
                (encoded(&key), encoded(&value)),
                Json::Array(vec![key, value]),
            )
        })
        .collect();
    keyed.sort_by(|left, right| left.0.cmp(&right.0));
    let tagged = keyed.into_iter().map(|(_, pair)| pair).collect();
    Json::Object(vec![(MAP_TAG.to_owned(), Json::Array(tagged))])
}

/// Returns a finite float with `-0.0` folded into `0.0`, rejecting NaN and infinity.
///
/// # Errors
///
/// Returns a SchemaError that shows the rejected value as Python's `repr` does.
pub fn checked_float(number: f64) -> CoreResult<f64> {
    if !number.is_finite() {
        let shown = py_float_repr(number);
        return Err(CoreError::new(
            ErrorKind::Schema,
            ErrorCode::Schema,
            format!("cannot canonicalize the non-finite number {shown}"),
            "replace NaN and infinity with finite numbers or None",
        )
        .with_context("value", shown));
    }
    Ok(if number == 0.0 { 0.0 } else { number })
}

/// Builds the error for a value nested deeper than MAX_NESTING levels, or holding a cycle.
#[must_use]
pub fn nesting_error() -> CoreError {
    CoreError::new(
        ErrorKind::Schema,
        ErrorCode::Schema,
        format!("value nests deeper than {MAX_NESTING} levels or holds a cycle"),
        "flatten the data or break the cycle",
    )
    .with_context("max_nesting", nesting_cap())
}

fn nesting_cap() -> i64 {
    i64::try_from(MAX_NESTING).unwrap_or(i64::MAX)
}

/// Builds the error for an int with more than MAX_INT_DIGITS digits, naming its bit length.
#[must_use]
pub fn int_too_long(bits: i64) -> CoreError {
    CoreError::new(
        ErrorKind::Schema,
        ErrorCode::Schema,
        format!("int has more than {MAX_INT_DIGITS} digits"),
        "store very large numbers as text",
    )
    .with_context("bits", bits)
    .with_context(
        "max_digits",
        i64::try_from(MAX_INT_DIGITS).unwrap_or(i64::MAX),
    )
}

/// Builds the error for text that holds a lone surrogate, which UTF-8 cannot encode.
///
/// `shown` is Python's `ascii` of the text's first CONTEXT_TEXT_CHARS characters.
#[must_use]
pub fn lone_surrogate(position: i64, shown: &str) -> CoreError {
    CoreError::new(
        ErrorKind::Schema,
        ErrorCode::Schema,
        format!("text holds a lone surrogate at index {position}"),
        "pass valid Unicode text",
    )
    .with_context("position", position)
    .with_context("text", shown)
}

/// Builds the error for a mapping key that starts with TAG_PREFIX.
///
/// `shown` and `escaped` are Python's `repr` and `ascii` of the key's first
/// CONTEXT_TEXT_CHARS characters.
#[must_use]
pub fn reserved_key(shown: &str, escaped: &str) -> CoreError {
    CoreError::new(
        ErrorKind::Schema,
        ErrorCode::Schema,
        format!("key {shown} uses the reserved prefix '{TAG_PREFIX}'"),
        format!("rename keys that start with '{TAG_PREFIX}'; it marks type tags"),
    )
    .with_context("key", escaped)
}

/// Builds the error for a value of a type the canonical form cannot hold.
#[must_use]
pub fn unsupported_type(name: &str, qualname: &str) -> CoreError {
    CoreError::new(
        ErrorKind::Schema,
        ErrorCode::Schema,
        format!("cannot canonicalize a value of type {name}"),
        SUPPORTED_HINT,
    )
    .with_context("type", qualname)
}

/// Builds the error for a model field of a type the canonical form cannot hold.
#[must_use]
pub fn unsupported_field(name: &str, qualname: &str) -> CoreError {
    CoreError::new(
        ErrorKind::Schema,
        ErrorCode::Schema,
        format!("cannot canonicalize a model field of type {name}"),
        SUPPORTED_HINT,
    )
    .with_context("type", qualname)
}

/// Builds the error for a model whose JSON dump failed, naming the model and the reason.
#[must_use]
pub fn model_dump_failed(model: &str, reason: &str) -> CoreError {
    CoreError::new(
        ErrorKind::Schema,
        ErrorCode::Schema,
        format!("cannot dump a {model} model to canonicalize it"),
        "pass a model whose fields serialize to JSON and hold no reference cycle",
    )
    .with_context("model", model)
    .with_context("reason", reason)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn text(value: &Json) -> String {
        String::from_utf8_lossy(&encoded(value)).into_owned()
    }

    #[test]
    fn numbers_follow_ecmascript_number_to_string() {
        let cases = [
            (5e-324, "5e-324"),
            (9_007_199_254_740_992.0, "9007199254740992"),
            (295_147_905_179_352_830_000.0, "295147905179352830000"),
            (1e21, "1e+21"),
            (1e23, "1e+23"),
            (0.000_001, "0.000001"),
            (333_333_333.333_333_3, "333333333.3333333"),
            (1e-7, "1e-7"),
            (1.5e-7, "1.5e-7"),
            (1.2345e20, "123450000000000000000"),
            (-2.5, "-2.5"),
            (1e300, "1e+300"),
            (-1.75e-300, "-1.75e-300"),
            (0.1, "0.1"),
            (-0.0, "0"),
        ];
        for (number, expected) in cases {
            assert_eq!(es_number(number), expected);
        }
    }

    #[test]
    fn objects_sort_keys_by_utf16_code_units() {
        let keys = [
            "\u{20ac}",
            "\r",
            "\u{fb33}",
            "1",
            "\u{1f600}",
            "\u{80}",
            "\u{f6}",
        ];
        let members = keys
            .iter()
            .map(|key| ((*key).to_owned(), Json::Int(0)))
            .collect();
        let expected = "{\"\\r\":0,\"1\":0,\"\u{80}\":0,\"\u{f6}\":0,\"\u{20ac}\":0,\
                        \"\u{1f600}\":0,\"\u{fb33}\":0}";
        assert_eq!(text(&Json::Object(members)), expected);
    }

    #[test]
    fn strings_carry_only_the_required_escapes() {
        let value = Json::Text("q\" b\\ \x08\x0c\n\r\t \x00\x1f \x7f \u{2028} /".to_owned());
        let expected = "\"q\\\" b\\\\ \\b\\f\\n\\r\\t \\u0000\\u001f \x7f \u{2028} /\"";
        assert_eq!(text(&value), expected);
    }

    #[test]
    fn tagged_pairs_sort_by_key_then_value_bytes() {
        let pairs = vec![
            (Json::Array(vec![Json::Int(1)]), Json::Text("x".to_owned())),
            (Json::Array(vec![Json::Int(1)]), Json::Text("a".to_owned())),
            (Json::Int(1), Json::Null),
        ];
        assert_eq!(
            text(&tagged_map(pairs)),
            "{\"$showpy:map\":[[1,null],[[1],\"a\"],[[1],\"x\"]]}"
        );
    }

    #[test]
    fn set_members_sort_by_canonical_bytes() {
        let members = vec![Json::Int(10), Json::Int(9), Json::Text("a".to_owned())];
        assert_eq!(
            sorted_members(members),
            vec![Json::Text("a".to_owned()), Json::Int(10), Json::Int(9)]
        );
    }

    #[test]
    fn floats_fold_negative_zero_and_reject_non_finite_values() {
        assert_eq!(checked_float(-0.0).map(f64::is_sign_negative), Ok(false));
        let refused = checked_float(f64::NEG_INFINITY).err();
        assert_eq!(
            refused.as_ref().map(CoreError::message),
            Some("cannot canonicalize the non-finite number -inf")
        );
    }
}

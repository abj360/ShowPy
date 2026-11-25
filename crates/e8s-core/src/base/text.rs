//! text --- the Unicode behavior of Python strings that identifier checks depend on
//!
//! Python decides which characters are whitespace, letters or printable, and how text
//! normalizes and case-folds, from its own Unicode tables. A host supplies those answers for
//! non-ASCII text, so the binding gives the answers Python gives; ASCII text never asks the
//! host.
//!
//! Contains:
//!     TextHost: answers the Unicode questions Python answers for non-ASCII text
//!     StdText: a host with no tables that refuses every non-ASCII question
//!     no_unicode_tables(): builds the error for a question the caller cannot answer
//!     is_space(): tells whether a character is Python whitespace
//!     is_word(): tells whether a character is a Python regex word character
//!     strip(): cuts Python whitespace off both ends of text
//!     casefold(): case-folds text as Python's `str.casefold` does
//!     nfc(): normalizes text to NFC
//!     str_repr(): writes text as Python's `repr` does

use std::borrow::Cow;

use super::error::{CoreError, CoreResult};
use super::error_codes::{ErrorCode, ErrorKind};

/// The ASCII characters Python's `str.isspace` accepts.
const ASCII_SPACE: [char; 10] = [
    ' ', '\t', '\n', '\r', '\x0b', '\x0c', '\x1c', '\x1d', '\x1e', '\x1f',
];

/// Builds the error for a Unicode question that the caller cannot answer.
#[must_use]
pub fn no_unicode_tables() -> CoreError {
    CoreError::new(
        ErrorKind::Schema,
        ErrorCode::Schema,
        "text outside ASCII needs Python's Unicode tables, which this caller does not have",
        "use the compiled module in Python, or pass ASCII text",
    )
}

/// Answers the Unicode questions Python answers for non-ASCII text.
///
/// Every answer may fail: a host that cannot answer, or whose answer raised, reports an error
/// and the caller stops, so no approximation ever stands in for Python's answer.
pub trait TextHost {
    /// Normalizes non-ASCII text to NFC.
    ///
    /// # Errors
    ///
    /// Returns the host's error when it cannot answer.
    fn nfc(&self, text: &str) -> CoreResult<String>;
    /// Case-folds non-ASCII text.
    ///
    /// # Errors
    ///
    /// Returns the host's error when it cannot answer.
    fn casefold(&self, text: &str) -> CoreResult<String>;
    /// Writes non-ASCII text as Python's `repr` does.
    ///
    /// # Errors
    ///
    /// Returns the host's error when it cannot answer.
    fn repr(&self, text: &str) -> CoreResult<String>;
    /// Tells whether a non-ASCII character is a letter, digit or number (`str.isalnum`).
    ///
    /// # Errors
    ///
    /// Returns the host's error when it cannot answer.
    fn is_alnum(&self, c: char) -> CoreResult<bool>;
    /// Tells whether a non-ASCII character is whitespace (`str.isspace`).
    ///
    /// # Errors
    ///
    /// Returns the host's error when it cannot answer.
    fn is_space(&self, c: char) -> CoreResult<bool>;
}

/// A host with no Unicode tables: it refuses every question about non-ASCII text.
///
/// ASCII text never reaches a host, so callers without Python (tests, fuzzing, a wasm build)
/// accept and write ASCII exactly as Python does and fail closed on anything else.
pub struct StdText;

impl TextHost for StdText {
    fn nfc(&self, _text: &str) -> CoreResult<String> {
        Err(no_unicode_tables())
    }

    fn casefold(&self, _text: &str) -> CoreResult<String> {
        Err(no_unicode_tables())
    }

    fn repr(&self, _text: &str) -> CoreResult<String> {
        Err(no_unicode_tables())
    }

    fn is_alnum(&self, _c: char) -> CoreResult<bool> {
        Err(no_unicode_tables())
    }

    fn is_space(&self, _c: char) -> CoreResult<bool> {
        Err(no_unicode_tables())
    }
}

/// Tells whether a character is Python whitespace.
///
/// # Errors
///
/// Returns the host's error for a non-ASCII character it cannot classify.
pub fn is_space(host: &dyn TextHost, c: char) -> CoreResult<bool> {
    if c.is_ascii() {
        Ok(ASCII_SPACE.contains(&c))
    } else {
        host.is_space(c)
    }
}

/// Tells whether a character is a Python regex word character: alphanumeric or `_`.
///
/// # Errors
///
/// Returns the host's error for a non-ASCII character it cannot classify.
pub fn is_word(host: &dyn TextHost, c: char) -> CoreResult<bool> {
    if c.is_ascii() {
        Ok(c.is_ascii_alphanumeric() || c == '_')
    } else {
        host.is_alnum(c)
    }
}

/// Cuts Python whitespace off both ends of text.
///
/// # Errors
///
/// Returns the host's error for a non-ASCII character it cannot classify.
pub fn strip<'a>(host: &dyn TextHost, text: &'a str) -> CoreResult<&'a str> {
    let mut start = text.len();
    for (at, c) in text.char_indices() {
        if !is_space(host, c)? {
            start = at;
            break;
        }
    }
    let mut end = start;
    for (at, c) in text[start..].char_indices().rev() {
        if !is_space(host, c)? {
            end = start + at + c.len_utf8();
            break;
        }
    }
    Ok(&text[start..end])
}

/// Case-folds text as Python's `str.casefold` does.
///
/// # Errors
///
/// Returns the host's error for non-ASCII text it cannot fold.
pub fn casefold<'a>(host: &dyn TextHost, text: &'a str) -> CoreResult<Cow<'a, str>> {
    if !text.is_ascii() {
        return host.casefold(text).map(Cow::Owned);
    }
    Ok(if text.bytes().any(|byte| byte.is_ascii_uppercase()) {
        Cow::Owned(text.to_ascii_lowercase())
    } else {
        Cow::Borrowed(text)
    })
}

/// Normalizes text to NFC; ASCII text is already normal.
///
/// # Errors
///
/// Returns the host's error for non-ASCII text it cannot normalize.
pub fn nfc<'a>(host: &dyn TextHost, text: &'a str) -> CoreResult<Cow<'a, str>> {
    if text.is_ascii() {
        Ok(Cow::Borrowed(text))
    } else {
        host.nfc(text).map(Cow::Owned)
    }
}

/// Writes text as Python's `repr` does.
///
/// # Errors
///
/// Returns the host's error for non-ASCII text it cannot write.
pub fn str_repr(host: &dyn TextHost, text: &str) -> CoreResult<String> {
    if text.is_ascii() {
        Ok(ascii_repr(text))
    } else {
        host.repr(text)
    }
}

fn ascii_repr(text: &str) -> String {
    let quote = if text.contains('\'') && !text.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut out = String::with_capacity(text.len() + 2);
    out.push(quote);
    for c in text.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '\t' => out.push_str("\\t"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            _ if c == quote => {
                out.push('\\');
                out.push(c);
            }
            _ if c.is_ascii_control() => out.push_str(&format!("\\x{:02x}", u32::from(c))),
            _ => out.push(c),
        }
    }
    out.push(quote);
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn repr_matches_python_for_ascii_text() {
        let cases = [
            ("a", "'a'"),
            ("it's", "\"it's\""),
            ("say \"hi\" it's", "'say \"hi\" it\\'s'"),
            ("a\nb\\", "'a\\nb\\\\'"),
            ("a\x01\x7f", "'a\\x01\\x7f'"),
        ];
        for (text, expected) in cases {
            assert_eq!(str_repr(&StdText, text).as_deref(), Ok(expected));
        }
    }

    #[test]
    fn whitespace_follows_python_not_rust() {
        assert_eq!(is_space(&StdText, '\x1c'), Ok(true));
        assert_eq!(is_space(&StdText, '\x0b'), Ok(true));
        assert_eq!(is_space(&StdText, 'a'), Ok(false));
        assert_eq!(strip(&StdText, " \x1c a b\n"), Ok("a b"));
        assert_eq!(strip(&StdText, " \t "), Ok(""));
    }

    #[test]
    fn casefold_borrows_text_that_is_already_folded() {
        assert!(matches!(casefold(&StdText, "abc"), Ok(Cow::Borrowed(_))));
        assert_eq!(casefold(&StdText, "ABC").as_deref(), Ok("abc"));
    }

    #[test]
    fn a_host_without_tables_refuses_every_non_ascii_question() {
        let text = "caf\u{e9}";
        assert_eq!(nfc(&StdText, text), Err(no_unicode_tables()));
        assert_eq!(casefold(&StdText, text), Err(no_unicode_tables()));
        assert_eq!(str_repr(&StdText, text), Err(no_unicode_tables()));
        assert_eq!(is_space(&StdText, '\u{a0}'), Err(no_unicode_tables()));
        assert_eq!(is_word(&StdText, '\u{e9}'), Err(no_unicode_tables()));
        assert_eq!(strip(&StdText, "\u{a0}a"), Err(no_unicode_tables()));
    }
}

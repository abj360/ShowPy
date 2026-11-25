//! text --- the Unicode behavior of Python strings that identifier checks depend on
//!
//! Python decides which characters are whitespace, letters or printable, and how text
//! normalizes and case-folds, from its own Unicode tables. A host supplies those answers for
//! non-ASCII text, so the binding gives the answers Python gives; ASCII text never asks the
//! host.
//!
//! Contains:
//!     TextHost: answers the Unicode questions Python answers for non-ASCII text
//!     StdText: approximates the answers with Rust's own tables, for tests and fuzzing
//!     is_space(): tells whether a character is Python whitespace
//!     is_word(): tells whether a character is a Python regex word character
//!     strip(): cuts Python whitespace off both ends of text
//!     casefold(): case-folds text as Python's `str.casefold` does
//!     nfc(): normalizes text to NFC
//!     str_repr(): writes text as Python's `repr` does

use std::borrow::Cow;

/// The ASCII characters Python's `str.isspace` accepts.
const ASCII_SPACE: [char; 10] = [
    ' ', '\t', '\n', '\r', '\x0b', '\x0c', '\x1c', '\x1d', '\x1e', '\x1f',
];

/// Answers the Unicode questions Python answers for non-ASCII text.
pub trait TextHost {
    /// Normalizes non-ASCII text to NFC.
    fn nfc(&self, text: &str) -> String;
    /// Case-folds non-ASCII text.
    fn casefold(&self, text: &str) -> String;
    /// Writes non-ASCII text as Python's `repr` does.
    fn repr(&self, text: &str) -> String;
    /// Tells whether a non-ASCII character is a letter, digit or number (`str.isalnum`).
    fn is_alnum(&self, c: char) -> bool;
    /// Tells whether a non-ASCII character is whitespace (`str.isspace`).
    fn is_space(&self, c: char) -> bool;
}

/// Approximates Python's Unicode answers with Rust's own tables.
///
/// Normalization is the identity and case folding is lowercasing, so text that Python would
/// change may differ; the binding's host gives Python's exact answers.
pub struct StdText;

impl TextHost for StdText {
    fn nfc(&self, text: &str) -> String {
        text.to_owned()
    }

    fn casefold(&self, text: &str) -> String {
        text.to_lowercase()
    }

    fn repr(&self, text: &str) -> String {
        repr_with(text, |c| !c.is_control())
    }

    fn is_alnum(&self, c: char) -> bool {
        c.is_alphanumeric()
    }

    fn is_space(&self, c: char) -> bool {
        c.is_whitespace()
    }
}

/// Tells whether a character is Python whitespace.
#[must_use]
pub fn is_space(host: &dyn TextHost, c: char) -> bool {
    if c.is_ascii() {
        ASCII_SPACE.contains(&c)
    } else {
        host.is_space(c)
    }
}

/// Tells whether a character is a Python regex word character: alphanumeric or `_`.
#[must_use]
pub fn is_word(host: &dyn TextHost, c: char) -> bool {
    if c.is_ascii() {
        c.is_ascii_alphanumeric() || c == '_'
    } else {
        host.is_alnum(c)
    }
}

/// Cuts Python whitespace off both ends of text.
#[must_use]
pub fn strip<'a>(host: &dyn TextHost, text: &'a str) -> &'a str {
    text.trim_matches(|c| is_space(host, c))
}

/// Case-folds text as Python's `str.casefold` does.
#[must_use]
pub fn casefold<'a>(host: &dyn TextHost, text: &'a str) -> Cow<'a, str> {
    if text.is_ascii() {
        if text.bytes().any(|byte| byte.is_ascii_uppercase()) {
            Cow::Owned(text.to_ascii_lowercase())
        } else {
            Cow::Borrowed(text)
        }
    } else {
        Cow::Owned(host.casefold(text))
    }
}

/// Normalizes text to NFC; ASCII text is already normal.
#[must_use]
pub fn nfc<'a>(host: &dyn TextHost, text: &'a str) -> Cow<'a, str> {
    if text.is_ascii() {
        Cow::Borrowed(text)
    } else {
        Cow::Owned(host.nfc(text))
    }
}

/// Writes text as Python's `repr` does.
#[must_use]
pub fn str_repr(host: &dyn TextHost, text: &str) -> String {
    if text.is_ascii() {
        repr_with(text, |c| c.is_ascii() && !c.is_ascii_control())
    } else {
        host.repr(text)
    }
}

fn repr_with(text: &str, is_printable: impl Fn(char) -> bool) -> String {
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
            _ if is_printable(c) => out.push(c),
            _ => out.push_str(&escape(c)),
        }
    }
    out.push(quote);
    out
}

fn escape(c: char) -> String {
    match u32::from(c) {
        code @ 0..=0xff => format!("\\x{code:02x}"),
        code @ 0x100..=0xffff => format!("\\u{code:04x}"),
        code => format!("\\U{code:08x}"),
    }
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
            assert_eq!(str_repr(&StdText, text), expected);
        }
    }

    #[test]
    fn whitespace_follows_python_not_rust() {
        assert!(is_space(&StdText, '\x1c'));
        assert!(is_space(&StdText, '\x0b'));
        assert!(!is_space(&StdText, 'a'));
        assert_eq!(strip(&StdText, " \x1c a b\n"), "a b");
    }

    #[test]
    fn casefold_borrows_text_that_is_already_folded() {
        assert!(matches!(casefold(&StdText, "abc"), Cow::Borrowed(_)));
        assert_eq!(casefold(&StdText, "ABC"), "abc");
    }
}

//! text_bytes --- Python text as bytes: UTF-8 that may also hold lone surrogates
//!
//! A Python string can hold a lone surrogate, which UTF-8 cannot. The binding hands such text
//! over encoded as Python's `surrogatepass` does: a surrogate becomes the three bytes UTF-8
//! would give its code point. Byte order then equals code point order, and every ASCII byte is
//! its own character, so the ID helpers split and compare these bytes as Python compares text.
//!
//! Contains:
//!     code_points(): decodes text bytes into code points
//!     first_char(): decodes the first code point and its length in bytes
//!     last_char(): decodes the last code point and its length in bytes
//!     char_prefix(): returns the bytes of the first `count` code points
//!     lossy(): views text bytes as a string, each surrogate as U+FFFD
//!     is_python_space(): tells whether a code point is Python whitespace
//!     strip_python_space(): cuts Python whitespace off both ends

use std::borrow::Cow;

use super::error::CoreResult;
use super::text::{TextHost, is_space};

const CONTINUATION_MASK: u8 = 0xc0;
const CONTINUATION_TAG: u8 = 0x80;
const SURROGATES: std::ops::RangeInclusive<u32> = 0xd800..=0xdfff;

fn is_continuation(byte: u8) -> bool {
    byte & CONTINUATION_MASK == CONTINUATION_TAG
}

/// Decodes the code point that starts at `bytes[0]`, with its length in bytes.
fn decode_at(bytes: &[u8]) -> Option<(u32, usize)> {
    let lead = *bytes.first()?;
    let (length, bits) = match lead {
        0x00..=0x7f => return Some((u32::from(lead), 1)),
        0xc0..=0xdf => (2, u32::from(lead & 0x1f)),
        0xe0..=0xef => (3, u32::from(lead & 0x0f)),
        _ => (4, u32::from(lead & 0x07)),
    };
    let tail = bytes.get(1..length)?;
    let point = tail
        .iter()
        .fold(bits, |acc, &byte| (acc << 6) | u32::from(byte & 0x3f));
    Some((point, length))
}

/// Decodes text bytes into code points, surrogates included.
#[must_use]
pub fn code_points(bytes: &[u8]) -> Vec<u32> {
    let mut points = Vec::with_capacity(bytes.len());
    let mut rest = bytes;
    while let Some((point, length)) = decode_at(rest) {
        points.push(point);
        rest = &rest[length..];
    }
    points
}

/// Decodes the first code point and its length in bytes; None for empty text.
#[must_use]
pub fn first_char(bytes: &[u8]) -> Option<(u32, usize)> {
    decode_at(bytes)
}

/// Decodes the last code point and its length in bytes; None for empty text.
#[must_use]
pub fn last_char(bytes: &[u8]) -> Option<(u32, usize)> {
    let start = bytes.iter().rposition(|&byte| !is_continuation(byte))?;
    decode_at(&bytes[start..])
}

/// Returns the bytes of the first `count` code points, as Python's `text[:count]` slices.
#[must_use]
pub fn char_prefix(bytes: &[u8], count: usize) -> &[u8] {
    let end = bytes
        .iter()
        .enumerate()
        .filter(|&(_, &byte)| !is_continuation(byte))
        .nth(count)
        .map_or(bytes.len(), |(index, _)| index);
    &bytes[..end]
}

/// Views text bytes as a string; each lone surrogate shows as one U+FFFD.
#[must_use]
pub fn lossy(bytes: &[u8]) -> Cow<'_, str> {
    if let Ok(text) = std::str::from_utf8(bytes) {
        return Cow::Borrowed(text);
    }
    Cow::Owned(
        code_points(bytes)
            .into_iter()
            .map(|point| char::from_u32(point).unwrap_or(char::REPLACEMENT_CHARACTER))
            .collect(),
    )
}

/// Tells whether a code point is Python whitespace; a surrogate never is.
///
/// # Errors
///
/// Returns the host's error when it cannot answer for a non-ASCII character.
pub fn is_python_space(host: &dyn TextHost, point: u32) -> CoreResult<bool> {
    if SURROGATES.contains(&point) {
        return Ok(false);
    }
    char::from_u32(point).map_or(Ok(false), |c| is_space(host, c))
}

/// Cuts Python whitespace off both ends, as `str.strip()` does.
///
/// # Errors
///
/// Returns the host's error when it cannot answer for a non-ASCII character.
pub fn strip_python_space<'a>(host: &dyn TextHost, bytes: &'a [u8]) -> CoreResult<&'a [u8]> {
    let mut text = bytes;
    while let Some((point, length)) = first_char(text) {
        if !is_python_space(host, point)? {
            break;
        }
        text = &text[length..];
    }
    while let Some((point, length)) = last_char(text) {
        if !is_python_space(host, point)? {
            break;
        }
        text = &text[..text.len() - length];
    }
    Ok(text)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::base::text::StdText;

    const SURROGATE: [u8; 3] = [0xed, 0xa0, 0x80];

    #[test]
    fn surrogates_decode_to_their_code_point() {
        let mut text = b"a".to_vec();
        text.extend_from_slice(&SURROGATE);
        text.extend_from_slice("\u{e9}".as_bytes());
        assert_eq!(code_points(&text), vec![0x61, 0xd800, 0xe9]);
        assert_eq!(last_char(&text), Some((0xe9, 2)));
        assert_eq!(char_prefix(&text, 2), &text[..4]);
        assert_eq!(lossy(&text), "a\u{fffd}\u{e9}");
    }

    #[test]
    fn strip_cuts_ascii_space_and_keeps_surrogates() {
        let mut text = b" \t".to_vec();
        text.extend_from_slice(&SURROGATE);
        text.extend_from_slice(b"x\x1c\n");
        assert_eq!(strip_python_space(&StdText, &text), Ok(&text[2..6]));
        assert_eq!(strip_python_space(&StdText, b"  "), Ok(&b""[..]));
    }

    #[test]
    fn prefixes_longer_than_the_text_keep_it_whole() {
        assert_eq!(char_prefix(b"abc", 5), b"abc");
        assert_eq!(char_prefix(b"abc", 0), b"");
    }
}

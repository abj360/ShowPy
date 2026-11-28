//! natural --- natural order of IDs: embedded numbers compare by value, so T2 sorts before T10
//!
//! Text is split into runs of ASCII digits and the text between them. A digit run's key is
//! `(0, len, digits)` after its leading zeros are dropped, so shorter numbers come first and
//! equal lengths compare digit by digit; any other run's key is `(1, 0, run)`. Keys compare
//! run by run, as Python compares tuples, and equal keys fall back to the whole text.
//!
//! Contains:
//!     Chunk: one run of a natural sort key
//!     natural_chunks(): splits text into the runs of its sort key
//!     natural_cmp(): compares two texts in natural order
//!     natural_order(): returns the indices that sort texts in natural order, stably

use std::cmp::Ordering;

/// Holds one run of a natural sort key.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Chunk<'a> {
    /// A run of ASCII digits, without its leading zeros (empty for a run of zeros).
    Number(&'a [u8]),
    /// A run of any other characters.
    Text(&'a [u8]),
}

impl Chunk<'_> {
    /// Returns the `(kind, length, text)` triple that orders runs as Python orders the key's
    /// tuples.
    fn key(&self) -> (u8, usize, &[u8]) {
        match *self {
            Chunk::Number(digits) => (0, digits.len(), digits),
            Chunk::Text(run) => (1, 0, run),
        }
    }
}

/// Splits text into the runs of its natural sort key, in order; empty runs are dropped.
#[must_use]
pub fn natural_chunks(text: &[u8]) -> Vec<Chunk<'_>> {
    let mut chunks = Vec::new();
    let mut start = 0;
    while start < text.len() {
        let is_digit = text[start].is_ascii_digit();
        let length = text[start..]
            .iter()
            .position(|byte| byte.is_ascii_digit() != is_digit)
            .unwrap_or(text.len() - start);
        let run = &text[start..start + length];
        chunks.push(if is_digit {
            let zeros = run.iter().take_while(|&&byte| byte == b'0').count();
            Chunk::Number(&run[zeros..])
        } else {
            Chunk::Text(run)
        });
        start += length;
    }
    chunks
}

fn compare_keys(left: &[Chunk<'_>], right: &[Chunk<'_>]) -> Ordering {
    left.iter()
        .map(Chunk::key)
        .cmp(right.iter().map(Chunk::key))
}

/// Compares two texts in natural order: by their keys, then by the texts themselves.
#[must_use]
pub fn natural_cmp(left: &[u8], right: &[u8]) -> Ordering {
    compare_keys(&natural_chunks(left), &natural_chunks(right)).then_with(|| left.cmp(right))
}

/// Returns the indices that sort `texts` in natural order; equal texts keep their order.
#[must_use]
pub fn natural_order(texts: &[&[u8]]) -> Vec<usize> {
    let keys: Vec<Vec<Chunk<'_>>> = texts.iter().map(|text| natural_chunks(text)).collect();
    let mut order: Vec<usize> = (0..texts.len()).collect();
    order.sort_by(|&a, &b| compare_keys(&keys[a], &keys[b]).then_with(|| texts[a].cmp(texts[b])));
    order
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sorted<'a>(texts: &[&'a str]) -> Vec<&'a str> {
        let bytes: Vec<&[u8]> = texts.iter().map(|text| text.as_bytes()).collect();
        natural_order(&bytes)
            .into_iter()
            .map(|index| texts[index])
            .collect()
    }

    #[test]
    fn numbers_compare_by_value_and_ties_by_text() {
        assert_eq!(
            sorted(&["T10", "T2", "B1", "T1"]),
            vec!["B1", "T1", "T2", "T10"]
        );
        assert_eq!(sorted(&["T2", "T02", "T002"]), vec!["T002", "T02", "T2"]);
    }

    #[test]
    fn digit_runs_sort_before_text_and_drop_leading_zeros() {
        assert_eq!(
            natural_chunks(b"T007x00"),
            vec![
                Chunk::Text(b"T"),
                Chunk::Number(b"7"),
                Chunk::Text(b"x"),
                Chunk::Number(b"")
            ]
        );
        assert_eq!(sorted(&["a", "1", ""]), vec!["", "1", "a"]);
    }

    #[test]
    fn huge_numbers_and_non_ascii_digits_sort_without_error() {
        let huge = format!("T{}", "9".repeat(5000));
        assert_eq!(sorted(&[huge.as_str(), "T1"]), vec!["T1", huge.as_str()]);
        assert_eq!(sorted(&["T1\u{b2}", "T1"]), vec!["T1", "T1\u{b2}"]);
    }
}

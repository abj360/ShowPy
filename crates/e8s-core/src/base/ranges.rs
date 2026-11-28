//! ranges --- ID ranges: an ID's trailing number, `T1-T8` expansion and run compaction
//!
//! IDs arrive as text bytes (see `text_bytes`): every character the rules look at is ASCII, so
//! byte positions stand in for character positions. Numbers are kept as their ASCII digits, so
//! an ID's number of up to ID_MAX_LENGTH digits never overflows.
//!
//! Contains:
//!     SplitId: an ID's prefix and the digits of its trailing number
//!     split_id(): splits an ID into its prefix and trailing ASCII number
//!     RangeIds: the IDs one range text names
//!     RangeIds::for_each_id(): writes each ID of the range in order
//!     parse_range(): expands `T1-T8` into IDs; a single ID names itself
//!     format_ranges(): compacts IDs into `T1-T4, T7`

use std::cmp::Ordering;

use super::caps::{MAX_RANGE, require_size, size_beyond_i64};
use super::decimal::{canonical, compare, count_between, increment, is_padded, padded_to};
use super::error::{CoreError, CoreResult};
use super::error_codes::{ErrorCode, ErrorKind};
use super::ids::ID_MAX_LENGTH;
use super::natural::natural_order;
use super::text::{TextHost, str_repr};
use super::text_bytes::{char_prefix, lossy, strip_python_space};

const RANGE_DASH: u8 = b'-';
const RUN_SEPARATOR: &[u8] = b", ";

/// Holds an ID's prefix and the digits of its trailing number, None without one.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct SplitId<'a> {
    /// The text before the trailing ASCII digits.
    pub prefix: &'a [u8],
    /// The trailing ASCII digits, leading zeros kept.
    pub digits: Option<&'a [u8]>,
}

/// Holds the IDs one range text names.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum RangeIds<'a> {
    /// Text that is not a range names one ID, itself without surrounding whitespace.
    Single(&'a [u8]),
    /// A range: `count` IDs `prefix` + number from `low` on, zero-padded to `width` digits.
    Run {
        /// The prefix both endpoints share.
        prefix: &'a [u8],
        /// The first number, without leading zeros.
        low: Vec<u8>,
        /// How many IDs the range holds, at most MAX_RANGE.
        count: usize,
        /// The digits each number is zero-padded to; 0 for unpadded ranges.
        width: usize,
    },
}

impl RangeIds<'_> {
    /// Writes each ID of the range, in order, into `each`, stopping at its first error.
    ///
    /// # Errors
    ///
    /// Returns the first error `each` returns.
    pub fn for_each_id<E>(&self, mut each: impl FnMut(&[u8]) -> Result<(), E>) -> Result<(), E> {
        let (prefix, low, count, width) = match self {
            Self::Single(text) => return each(text),
            Self::Run {
                prefix,
                low,
                count,
                width,
            } => (prefix, low, *count, *width),
        };
        let mut number = low.clone();
        let mut id = Vec::with_capacity(prefix.len() + width.max(number.len()) + 1);
        for index in 0..count {
            if index > 0 {
                increment(&mut number);
            }
            id.clear();
            id.extend_from_slice(prefix);
            padded_to(&number, width, &mut id);
            each(&id)?;
        }
        Ok(())
    }
}

/// Writes text bytes as Python's `repr` does; a lone surrogate shows as U+FFFD.
fn shown(host: &dyn TextHost, text: &[u8]) -> CoreResult<String> {
    str_repr(host, &lossy(text))
}

fn require_digit_run(host: &dyn TextHost, digits: &[u8], text: &[u8]) -> CoreResult<()> {
    if digits.len() <= ID_MAX_LENGTH {
        return Ok(());
    }
    let count = i64::try_from(digits.len()).unwrap_or(i64::MAX);
    Err(CoreError::new(
        ErrorKind::Schema,
        ErrorCode::Schema,
        format!(
            "number in {}... has {count} digits",
            shown(host, char_prefix(text, ID_MAX_LENGTH))?
        ),
        format!("IDs hold at most {ID_MAX_LENGTH} characters, so no number is longer"),
    )
    .with_context("digits", count)
    .with_context("max", ID_MAX_LENGTH as i64))
}

/// Splits an ID into its prefix and trailing ASCII number, in time linear in its length.
///
/// # Errors
///
/// Returns a SchemaError for a trailing number longer than any ID can hold.
pub fn split_id<'a>(host: &dyn TextHost, text: &'a [u8]) -> CoreResult<SplitId<'a>> {
    let length = text
        .iter()
        .rev()
        .take_while(|byte| byte.is_ascii_digit())
        .count();
    if length == 0 {
        return Ok(SplitId {
            prefix: text,
            digits: None,
        });
    }
    let (prefix, digits) = text.split_at(text.len() - length);
    require_digit_run(host, digits, text)?;
    Ok(SplitId {
        prefix,
        digits: Some(digits),
    })
}

fn range_error(host: &dyn TextHost, text: &[u8], hint: &'static str) -> CoreResult<CoreError> {
    Ok(CoreError::new(
        ErrorKind::Schema,
        ErrorCode::Schema,
        format!("bad range {}", shown(host, text)?),
        hint,
    )
    .with_context("text", lossy(text).into_owned()))
}

fn bad_range(host: &dyn TextHost, text: &[u8]) -> CoreResult<CoreError> {
    range_error(
        host,
        text,
        "use one prefix and an ascending range, e.g. T1-T8",
    )
}

fn mixed_padding(host: &dyn TextHost, text: &[u8]) -> CoreResult<CoreError> {
    Ok(CoreError::new(
        ErrorKind::Schema,
        ErrorCode::RangePadding,
        format!(
            "range {} mixes zero-padded and unpadded endpoints",
            shown(host, text)?
        ),
        "spell both endpoints alike: T01-T10 (padded) or T1-T10 (unpadded)",
    )
    .with_context("text", lossy(text).into_owned()))
}

/// Finds the first `-` whose left side ends in digits and whose right side is the left side's
/// prefix followed by digits only; returns the prefix and both digit runs.
fn range_endpoints(text: &[u8]) -> Option<(&[u8], &[u8], &[u8])> {
    for (position, &byte) in text.iter().enumerate() {
        if byte != RANGE_DASH {
            continue;
        }
        let digits = text[..position]
            .iter()
            .rev()
            .take_while(|byte| byte.is_ascii_digit())
            .count();
        let start = position - digits;
        let prefix = &text[..start];
        if digits == 0 || !text[position + 1..].starts_with(prefix) {
            continue;
        }
        let last = &text[position + 1 + prefix.len()..];
        if !last.is_empty() && last.iter().all(u8::is_ascii_digit) {
            return Some((prefix, &text[start..position], last));
        }
    }
    None
}

/// Tells whether text reads as `letters digits - letters digits`, a range whose prefixes differ.
fn is_mismatched_range(text: &[u8]) -> bool {
    fn id_part(text: &[u8]) -> bool {
        let letters = text
            .iter()
            .take_while(|byte| byte.is_ascii_alphabetic() || **byte == b'_')
            .count();
        letters > 0 && text.len() > letters && text[letters..].iter().all(u8::is_ascii_digit)
    }
    text.iter()
        .enumerate()
        .filter(|&(_, &byte)| byte == RANGE_DASH)
        .any(|(position, _)| id_part(&text[..position]) && id_part(&text[position + 1..]))
}

fn range_width(host: &dyn TextHost, first: &[u8], last: &[u8], text: &[u8]) -> CoreResult<usize> {
    if !is_padded(first) {
        if is_padded(last) {
            return Err(mixed_padding(host, text)?);
        }
        return Ok(0);
    }
    let width = first.len();
    if last.len() < width || (is_padded(last) && last.len() != width) {
        return Err(mixed_padding(host, text)?);
    }
    Ok(width)
}

/// Expands an ID range such as `T1-T8` or `P1x1-P1x3`; a single ID names itself.
///
/// The range splits at the `-` whose two sides each end in digits and share a prefix, so IDs
/// with an inner `-` such as `flag-1-flag-3` expand too. Both endpoints are spelled alike:
/// padded to one width (`T01-T10`) or both unpadded (`T1-T10`).
///
/// # Errors
///
/// Returns a SchemaError for empty text, a dangling `-`, prefixes that differ, a descending
/// range, an endpoint longer than any ID, mixed padding (code RANGE_PADDING) and a range of
/// more than MAX_RANGE IDs.
pub fn parse_range<'a>(host: &dyn TextHost, text: &'a [u8]) -> CoreResult<RangeIds<'a>> {
    let stripped = strip_python_space(host, text)?;
    if stripped.first().is_none_or(|&byte| byte == RANGE_DASH) || stripped.ends_with(b"-") {
        return Err(range_error(host, text, "write ranges as T1-T8")?);
    }
    let Some((prefix, first, last)) = range_endpoints(stripped) else {
        if is_mismatched_range(stripped) {
            return Err(bad_range(host, text)?);
        }
        return Ok(RangeIds::Single(stripped));
    };
    require_digit_run(host, first, text)?;
    require_digit_run(host, last, text)?;
    if compare(first, last) == Ordering::Greater {
        return Err(bad_range(host, text)?);
    }
    let width = range_width(host, first, last, text)?;
    let total = count_between(canonical(first), canonical(last));
    let digits = String::from_utf8_lossy(&total);
    let count = digits
        .parse::<i64>()
        .map_err(|_| size_beyond_i64(&digits, MAX_RANGE, "range IDs"))?;
    require_size(count, MAX_RANGE, "range IDs")?;
    Ok(RangeIds::Run {
        prefix,
        low: canonical(first).to_vec(),
        count: usize::try_from(count).unwrap_or(0),
        width,
    })
}

/// Tells whether `current` continues the run from `first` to `previous`: one prefix, the next
/// number, spelled at the width of the run's first ID.
fn continues(
    host: &dyn TextHost,
    first: &[u8],
    previous: &[u8],
    current: &[u8],
) -> CoreResult<bool> {
    let (head, left, right) = (
        split_id(host, first)?,
        split_id(host, previous)?,
        split_id(host, current)?,
    );
    let (Some(head_digits), Some(left_digits), Some(right_digits)) =
        (head.digits, left.digits, right.digits)
    else {
        return Ok(false);
    };
    if head.prefix != left.prefix || left.prefix != right.prefix {
        return Ok(false);
    }
    let mut next = canonical(left_digits).to_vec();
    increment(&mut next);
    if canonical(right_digits) != next.as_slice() {
        return Ok(false);
    }
    let width = if is_padded(head_digits) {
        head_digits.len()
    } else {
        0
    };
    let mut spelled = Vec::with_capacity(width.max(next.len()));
    padded_to(&next, width, &mut spelled);
    Ok(right_digits == spelled.as_slice())
}

/// Compacts IDs into comma-separated runs that `parse_range` reads back, run by run.
///
/// Duplicates collapse and the result is in natural order; a run continues only while each ID
/// is the next number in the spelling of the run's first ID.
///
/// # Errors
///
/// Returns split_id's SchemaError for an ID whose trailing number no ID can hold.
pub fn format_ranges(host: &dyn TextHost, ids: &[&[u8]]) -> CoreResult<Vec<u8>> {
    let mut sorted: Vec<&[u8]> = natural_order(ids).into_iter().map(|i| ids[i]).collect();
    sorted.dedup();
    let mut runs: Vec<(&[u8], &[u8])> = Vec::new();
    for &id in &sorted {
        if let Some(run) = runs.last_mut()
            && continues(host, run.0, run.1, id)?
        {
            run.1 = id;
            continue;
        }
        runs.push((id, id));
    }
    let mut text = Vec::new();
    for (index, (first, last)) in runs.iter().enumerate() {
        if index > 0 {
            text.extend_from_slice(RUN_SEPARATOR);
        }
        text.extend_from_slice(first);
        if first != last {
            text.push(RANGE_DASH);
            text.extend_from_slice(last);
        }
    }
    Ok(text)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::base::text::StdText;

    fn expand(text: &str) -> CoreResult<Vec<String>> {
        let mut ids = Vec::new();
        parse_range(&StdText, text.as_bytes())?.for_each_id(|id| {
            ids.push(String::from_utf8_lossy(id).into_owned());
            Ok::<_, CoreError>(())
        })?;
        Ok(ids)
    }

    fn compact(ids: &[&str]) -> CoreResult<String> {
        let views: Vec<&[u8]> = ids.iter().map(|id| id.as_bytes()).collect();
        Ok(String::from_utf8_lossy(&format_ranges(&StdText, &views)?).into_owned())
    }

    #[test]
    fn ranges_expand_with_their_padding_and_inner_dashes() {
        assert_eq!(
            expand("T1-T3"),
            Ok(vec!["T1".into(), "T2".into(), "T3".into()])
        );
        assert_eq!(
            expand("T08-T10"),
            Ok(vec!["T08".into(), "T09".into(), "T10".into()])
        );
        assert_eq!(
            expand("flag-1-flag-2"),
            Ok(vec!["flag-1".into(), "flag-2".into()])
        );
        assert_eq!(expand(" lead "), Ok(vec!["lead".into()]));
        assert_eq!(expand("T1-T10000").map(|ids| ids.len()), Ok(10_000));
    }

    #[test]
    fn bad_ranges_are_refused_with_their_codes() {
        for text in ["", "-", "T1-", "T4-T1", "T1-B4", "T1-T10001"] {
            assert!(expand(text).is_err(), "{text}");
        }
        let Err(err) = expand("T1-T010") else {
            unreachable!("mixed padding passed");
        };
        assert_eq!(err.code(), ErrorCode::RangePadding);
        let Err(huge) = expand("T1-T100000000000000000000000") else {
            unreachable!("a huge range passed");
        };
        assert!(huge.message().starts_with("range IDs would need 1000000"));
    }

    #[test]
    fn split_id_keeps_leading_zeros_and_refuses_overlong_numbers() {
        let parts = split_id(&StdText, b"T007");
        assert_eq!(
            parts,
            Ok(SplitId {
                prefix: b"T",
                digits: Some(b"007")
            })
        );
        let long = format!("T{}", "9".repeat(ID_MAX_LENGTH + 1));
        assert!(split_id(&StdText, long.as_bytes()).is_err());
    }

    #[test]
    fn runs_compact_in_the_spelling_of_their_first_id() {
        assert_eq!(compact(&["T1", "T2", "T3", "T7"]), Ok("T1-T3, T7".into()));
        assert_eq!(compact(&["T09", "T10", "T9"]), Ok("T09, T9-T10".into()));
        assert_eq!(compact(&[]), Ok(String::new()));
    }
}

//! refs --- reference (`kind:id@version`) and transition (`12→13`) parsing and formatting
//!
//! Contains:
//!     ARROW: the canonical separator of a transition ID
//!     RefParts: holds the kind, id and version of one reference
//!     parse_ref(): reads `kind:id@version` into its parts, checking the id and version
//!     format_ref(): writes a reference from its parts
//!     split_tid(): reads `12→13` or `12->13` into its two set IDs
//!     bad_tid(): builds the error for transition ID text that is not two set IDs
//!     bad_tid_pair(): builds the error for a transition ID tuple that is not a pair
//!     format_tid(): writes two set IDs as `12→13`

use super::error::{CoreError, CoreResult};
use super::error_codes::{ErrorCode, ErrorKind};
use super::ids::{IdRule, check_text};
use super::text::{TextHost, is_space, is_word, str_repr, strip};

/// The canonical separator of a transition ID.
pub const ARROW: &str = "→";
const ASCII_ARROW: &str = "->";
const REF_ID_KIND: &str = "reference";
const REF_VERSION_KIND: &str = "reference version";

/// Holds the kind, id and version of one reference.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct RefParts {
    /// The reference kind, as written.
    pub kind: String,
    /// The reference ID, NFC-normalized and checked.
    pub id: String,
    /// The version, NFC-normalized and checked; None when the text has none.
    pub version: Option<String>,
}

/// Splits stripped text at `kind:id@version`; None when the text has another shape.
fn split_ref<'a>(
    host: &dyn TextHost,
    text: &'a str,
) -> Option<(&'a str, &'a str, Option<&'a str>)> {
    let mut chars = text.char_indices();
    if !chars.next()?.1.is_ascii_alphabetic() {
        return None;
    }
    let colon = loop {
        match chars.next()? {
            (at, ':') => break at,
            (_, c) if c == '-' || is_word(host, c) => {}
            _ => return None,
        }
    };
    let rest = &text[colon + 1..];
    let id_end = rest
        .char_indices()
        .find(|(_, c)| matches!(c, '@' | ':') || is_space(host, *c))
        .map_or(rest.len(), |(at, _)| at);
    if id_end == 0 {
        return None;
    }
    let (id, tail) = rest.split_at(id_end);
    let kind = &text[..colon];
    if tail.is_empty() {
        return Some((kind, id, None));
    }
    let version = tail.strip_prefix('@')?;
    let is_bad = |c: char| c == '@' || is_space(host, c);
    if version.is_empty() || version.chars().any(is_bad) {
        return None;
    }
    Some((kind, id, Some(version)))
}

/// Reads a reference such as `sheet:hs-field@2`.
///
/// The id must pass the sheet ID check and the version its own rule (letters, digits, `.`,
/// `-` and `+`), so `..`, `/` and spaces never reach a path.
///
/// # Errors
///
/// Returns a SchemaError for text that is not `kind:id` or `kind:id@version`, or whose id or
/// version fails its check.
pub fn parse_ref(host: &dyn TextHost, text: &str) -> CoreResult<RefParts> {
    let Some((kind, id, version)) = split_ref(host, strip(host, text)) else {
        return Err(CoreError::new(
            ErrorKind::Schema,
            ErrorCode::Schema,
            format!("bad reference {}", str_repr(host, text)),
            "write references as kind:id or kind:id@version",
        )
        .with_context("text", text));
    };
    let id = check_text(host, IdRule::Sheet, id, REF_ID_KIND)?;
    let version = version
        .map(|version| check_text(host, IdRule::Version, version, REF_VERSION_KIND))
        .transpose()?;
    Ok(RefParts {
        kind: kind.to_owned(),
        id,
        version,
    })
}

/// Writes a reference from its parts, after checking that it reads back.
///
/// # Errors
///
/// Returns the SchemaError of `parse_ref` for the written text.
pub fn format_ref(
    host: &dyn TextHost,
    kind: &str,
    ref_id: &str,
    version: Option<&str>,
) -> CoreResult<String> {
    let text = match version {
        None => format!("{kind}:{ref_id}"),
        Some(version) => format!("{kind}:{ref_id}@{version}"),
    };
    parse_ref(host, &text)?;
    Ok(text)
}

/// Builds the error for a transition ID text that does not split into two non-empty parts.
#[must_use]
pub fn bad_tid(shown: &str) -> CoreError {
    CoreError::new(
        ErrorKind::Schema,
        ErrorCode::Schema,
        format!("bad transition id {shown}"),
        "write transitions as 12→13 or 12->13",
    )
}

/// Builds the error for a transition ID tuple that is not a pair.
#[must_use]
pub fn bad_tid_pair(shown: &str) -> CoreError {
    CoreError::new(
        ErrorKind::Schema,
        ErrorCode::Schema,
        format!("bad transition id {shown}"),
        "pass a pair of set IDs, such as ('12', '13')",
    )
}

/// Reads a transition ID written with `→` or `->`; a third part is refused.
///
/// Only the shape is checked: stored pins and paths name sets that may predate the ID rule (a
/// v1 set `con`).
///
/// # Errors
///
/// Returns a SchemaError unless the text splits into exactly two non-empty parts.
pub fn split_tid(host: &dyn TextHost, text: &str) -> CoreResult<(String, String)> {
    let normalized = text.replace(ASCII_ARROW, ARROW);
    let parts: Vec<&str> = normalized
        .split(ARROW)
        .map(|part| strip(host, part))
        .collect();
    match parts.as_slice() {
        [from, to] if !from.is_empty() && !to.is_empty() => {
            Ok(((*from).to_owned(), (*to).to_owned()))
        }
        _ => Err(bad_tid(&str_repr(host, text)).with_context("text", text)),
    }
}

/// Writes two set IDs as `12→13`.
#[must_use]
pub fn format_tid(from_set: &str, to_set: &str) -> String {
    format!("{from_set}{ARROW}{to_set}")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::base::text::StdText;

    fn parts(text: &str) -> Option<(String, String, Option<String>)> {
        let parsed = parse_ref(&StdText, text).ok()?;
        Some((parsed.kind, parsed.id, parsed.version))
    }

    #[test]
    fn references_read_with_and_without_a_version() {
        let expected = ("sheet".to_owned(), "gym".to_owned(), Some("1".to_owned()));
        assert_eq!(parts("sheet:gym@1"), Some(expected));
        assert_eq!(
            parts(" sheet:a-b "),
            Some(("sheet".to_owned(), "a-b".to_owned(), None))
        );
        assert_eq!(
            parts("sheet:a@1.0.0+build.5").and_then(|p| p.2).as_deref(),
            Some("1.0.0+build.5")
        );
    }

    #[test]
    fn malformed_references_are_refused() {
        let bad = [
            "field",
            "sheet:",
            ":x",
            "sheet:a@",
            "sheet:a@b@c",
            "sheet:..",
            "sheet:a/b",
            "sheet:a@1..2",
            "sheet:a@all",
            "sheet:a@+1",
            "sheet:a b@1",
            "9x:a",
            "sheet:a:b",
        ];
        for text in bad {
            assert!(parse_ref(&StdText, text).is_err(), "{text}");
        }
    }

    #[test]
    fn transitions_split_on_either_arrow() {
        for text in ["12→13", "12->13", " 12 -> 13 "] {
            assert_eq!(
                split_tid(&StdText, text),
                Ok(("12".to_owned(), "13".to_owned()))
            );
        }
        for text in ["12", "12→", "→13", "1→2→3"] {
            assert!(split_tid(&StdText, text).is_err(), "{text}");
        }
        assert_eq!(format_tid("12", "13"), "12→13");
    }

    #[test]
    fn format_ref_checks_what_it_writes() {
        assert_eq!(
            format_ref(&StdText, "sheet", "gym", Some("2")).as_deref(),
            Ok("sheet:gym@2")
        );
        assert!(format_ref(&StdText, "sheet", "con", None).is_err());
    }
}

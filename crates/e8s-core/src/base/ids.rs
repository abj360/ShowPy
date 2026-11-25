//! ids --- the ID rules: shape, length, reserved names and the check that applies them
//!
//! Contains:
//!     IdRule: names the three ID shapes (spot-like IDs, sheet IDs, reference versions)
//!     ID_MAX_LENGTH: bounds the characters of one ID
//!     ID_PATTERN: the source of the regular expression that states the ID shape
//!     SHEET_ID_PATTERN: the source of the sheet ID shape
//!     VERSION_PATTERN: the source of the reference version shape
//!     RESERVED_IDS: lists the names no ID may take, in lowercase
//!     is_device_name(): tells whether text is a Windows device name, alone or before a dot
//!     check_normalized(): checks text that is already NFC against one rule
//!     check_id(): validates one ID and returns its NFC-normalized text
//!     check_sheet_id(): validates one sheet item or sheet reference ID the same way
//!     check_text(): validates text against any rule and returns its NFC-normalized form
//!     find_case_clashes(): lists pairs of IDs that differ only by case

use std::collections::{BTreeMap, BTreeSet};

use super::error::{CoreError, CoreResult};
use super::error_codes::{ErrorCode, ErrorKind};
use super::text::{TextHost, casefold, nfc, str_repr};

/// Bounds the characters of one ID.
pub const ID_MAX_LENGTH: usize = 64;
/// The source of the regular expression that states the ID shape; test with fullmatch.
pub const ID_PATTERN: &str = r"^[A-Za-z0-9_](?:[A-Za-z0-9_.]{0,62}[A-Za-z0-9_])?$";
/// The source of the shape of sheet item and sheet reference IDs, which may hold an inner `-`.
pub const SHEET_ID_PATTERN: &str = r"^[A-Za-z0-9_](?:[A-Za-z0-9_.\-]{0,62}[A-Za-z0-9_])?$";
/// The source of the shape of a reference version, which may also hold `+` build metadata.
pub const VERSION_PATTERN: &str = r"^[A-Za-z0-9](?:[A-Za-z0-9.+\-]{0,62}[A-Za-z0-9])?$";
/// Lists the names no ID may take, in lowercase.
pub const RESERVED_IDS: [&str; 24] = [
    "all", "props", "con", "prn", "aux", "nul", "com1", "com2", "com3", "com4", "com5", "com6",
    "com7", "com8", "com9", "lpt1", "lpt2", "lpt3", "lpt4", "lpt5", "lpt6", "lpt7", "lpt8", "lpt9",
];
const DOUBLE_DOT: &str = "..";
const CONTEXT_TEXT_CHARS: usize = 80;
const RESERVED_HINT: &str = "not all, props or a Windows device name (con, com1, con.txt)";

/// Names the three ID shapes.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum IdRule {
    /// Spot, set, form, route, note and selection IDs: letters, digits, `_` and `.`.
    Id,
    /// Sheet items and references and show IDs: the ID shape with an inner `-` too.
    Sheet,
    /// Reference versions: letters, digits, `.`, `-` and `+`.
    Version,
}

impl IdRule {
    fn pattern(self) -> &'static str {
        match self {
            Self::Id => ID_PATTERN,
            Self::Sheet => SHEET_ID_PATTERN,
            Self::Version => VERSION_PATTERN,
        }
    }

    fn is_body(self, c: char) -> bool {
        let extra = match self {
            Self::Id => matches!(c, '_' | '.'),
            Self::Sheet => matches!(c, '_' | '.' | '-'),
            Self::Version => matches!(c, '.' | '+' | '-'),
        };
        c.is_ascii_alphanumeric() || extra
    }

    fn is_edge(self, c: char) -> bool {
        match self {
            Self::Id => c == '.',
            Self::Sheet => matches!(c, '.' | '-'),
            Self::Version => matches!(c, '.' | '-' | '+'),
        }
    }

    fn may_end(self, c: char) -> bool {
        c.is_ascii_alphanumeric() || (c == '_' && self != Self::Version)
    }

    fn hint(self) -> String {
        let pattern = self.pattern();
        match self {
            Self::Id => format!(
                "use 1 to 64 ASCII letters, digits, _ and . matching {pattern} (no leading or \
                 trailing . and no ..), {RESERVED_HINT}"
            ),
            Self::Sheet => format!(
                "use 1 to 64 ASCII letters, digits, _, . and - matching {pattern} (no leading or \
                 trailing . or - and no ..), {RESERVED_HINT}"
            ),
            Self::Version => format!(
                "use 1 to 64 ASCII letters, digits, . - and + matching {pattern} (a letter or \
                 digit at both ends and no ..), {RESERVED_HINT}"
            ),
        }
    }
}

fn is_device(name: &str) -> bool {
    let is_com_or_lpt = |prefix: &str| {
        name.strip_prefix(prefix)
            .is_some_and(|digit| matches!(digit.as_bytes(), [b'1'..=b'9']))
    };
    matches!(name, "con" | "prn" | "aux" | "nul") || is_com_or_lpt("com") || is_com_or_lpt("lpt")
}

/// Tells whether text is a Windows device name, alone or before a dot, ignoring case.
///
/// Windows opens `con`, and `con.txt` too, as the console device instead of a file or folder.
#[must_use]
pub fn is_device_name(host: &dyn TextHost, text: &str) -> bool {
    let folded = casefold(host, text);
    is_device(folded.split('.').next().unwrap_or_default())
}

fn is_reserved(host: &dyn TextHost, text: &str) -> bool {
    let folded = casefold(host, text);
    let head = folded.split('.').next().unwrap_or_default();
    matches!(folded.as_ref(), "all" | "props") || is_device(head)
}

fn matches_shape(rule: IdRule, text: &str) -> bool {
    let (Some(first), Some(last)) = (text.chars().next(), text.chars().next_back()) else {
        return false;
    };
    text.is_ascii()
        && text.len() <= ID_MAX_LENGTH
        && rule.may_end(first)
        && rule.may_end(last)
        && text.chars().all(|c| rule.is_body(c))
}

fn passes(host: &dyn TextHost, text: &str, rule: IdRule) -> bool {
    matches_shape(rule, text) && !text.contains(DOUBLE_DOT) && !is_reserved(host, text)
}

/// Finds the character index of the first character that breaks the rule in text that fails it.
fn first_bad_position(host: &dyn TextHost, text: &str, rule: IdRule) -> usize {
    let chars: Vec<char> = text.chars().collect();
    let (Some(first), Some(last)) = (chars.first(), chars.last()) else {
        return 0;
    };
    if rule.is_edge(*first) || is_reserved(host, text) {
        return 0;
    }
    let mut candidates = Vec::new();
    if rule.is_edge(*last) {
        candidates.push(chars.len() - 1);
    }
    candidates.extend(chars.iter().position(|c| !rule.is_body(*c)));
    if chars.len() > ID_MAX_LENGTH {
        candidates.push(ID_MAX_LENGTH);
    }
    candidates.extend(
        text.find(DOUBLE_DOT)
            .map(|byte| text[..byte].chars().count() + 1),
    );
    candidates.into_iter().min().unwrap_or(0)
}

/// Checks text that is already NFC against one rule.
///
/// # Errors
///
/// Returns a SchemaError naming `kind`, the text (cut to 80 characters), the position of the
/// first bad character and the rule's pattern.
pub fn check_normalized(
    host: &dyn TextHost,
    rule: IdRule,
    text: &str,
    kind: &str,
) -> CoreResult<()> {
    if passes(host, text, rule) {
        return Ok(());
    }
    let shown: String = text.chars().take(CONTEXT_TEXT_CHARS).collect();
    let position = i64::try_from(first_bad_position(host, text, rule)).unwrap_or(i64::MAX);
    Err(CoreError::new(
        ErrorKind::Schema,
        ErrorCode::Schema,
        format!("bad {kind} id {}", str_repr(host, &shown)),
        rule.hint(),
    )
    .with_context("kind", kind)
    .with_context("text", shown)
    .with_context("position", position)
    .with_context("pattern", rule.pattern()))
}

/// Validates text against a rule and returns its NFC-normalized form.
///
/// # Errors
///
/// Returns the SchemaError of `check_normalized` for the normalized text.
pub fn check_text(host: &dyn TextHost, rule: IdRule, text: &str, kind: &str) -> CoreResult<String> {
    let normalized = nfc(host, text);
    check_normalized(host, rule, &normalized, kind)?;
    Ok(normalized.into_owned())
}

/// Validates one ID against the ID pattern and returns its NFC-normalized text.
///
/// Reserved names are rejected, and so is a Windows device name followed by an extension
/// (`con.5`), which Windows opens as the device.
///
/// # Errors
///
/// Returns the SchemaError of `check_normalized` under the ID rule.
pub fn check_id(host: &dyn TextHost, text: &str, kind: &str) -> CoreResult<String> {
    check_text(host, IdRule::Id, text, kind)
}

/// Validates one sheet item or sheet reference ID and returns its NFC-normalized text.
///
/// The rule is `check_id`'s, except that `-` may appear anywhere but the first or last
/// character.
///
/// # Errors
///
/// Returns the SchemaError of `check_normalized` under the sheet rule.
pub fn check_sheet_id(host: &dyn TextHost, text: &str, kind: &str) -> CoreResult<String> {
    check_text(host, IdRule::Sheet, text, kind)
}

/// Lists pairs of distinct IDs that are equal once case is folded.
///
/// Returns the pairs `(a, b)` with `a < b`, sorted.
#[must_use]
pub fn find_case_clashes<'a>(
    host: &dyn TextHost,
    ids: impl IntoIterator<Item = &'a str>,
) -> Vec<(String, String)> {
    let mut folded: BTreeMap<String, BTreeSet<&str>> = BTreeMap::new();
    for id in ids {
        folded
            .entry(casefold(host, id).into_owned())
            .or_default()
            .insert(id);
    }
    let mut pairs = Vec::new();
    for group in folded.values().filter(|group| group.len() > 1) {
        let members: Vec<&str> = group.iter().copied().collect();
        for (at, first) in members.iter().enumerate() {
            pairs.extend(
                members[at + 1..]
                    .iter()
                    .map(|second| ((*first).to_owned(), (*second).to_owned())),
            );
        }
    }
    pairs.sort();
    pairs
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::base::text::StdText;

    fn position(text: &str) -> Option<i64> {
        let Err(err) = check_normalized(&StdText, IdRule::Id, text, "set") else {
            return None;
        };
        err.context()
            .iter()
            .find_map(|(key, value)| match (key.as_str(), value) {
                ("position", crate::base::ContextValue::Int(at)) => Some(*at),
                _ => None,
            })
    }

    #[test]
    fn every_reserved_name_is_rejected_as_an_id() {
        for name in RESERVED_IDS {
            assert!(check_id(&StdText, name, "set").is_err(), "{name}");
            assert!(
                check_id(&StdText, &name.to_uppercase(), "set").is_err(),
                "{name}"
            );
        }
    }

    #[test]
    fn positions_match_the_python_checker() {
        let cases = [
            ("", 0),
            ("a-b", 1),
            (".a", 0),
            ("a.", 1),
            ("a..b", 2),
            ("..", 0),
            ("..a", 0),
            ("ALL", 0),
            ("com9", 0),
            ("T*", 1),
            ("group:1", 5),
        ];
        for (text, expected) in cases {
            assert_eq!(position(text), Some(expected), "{text}");
        }
        assert_eq!(position(&"a".repeat(65)), Some(64));
        assert_eq!(position("a\n"), Some(1));
    }

    #[test]
    fn accepted_ids_include_names_that_only_start_like_a_device() {
        for text in [
            "12A", "x_y", "cons.5", "com10", "com0.1", "all.1", "a.con", "props2",
        ] {
            assert_eq!(check_id(&StdText, text, "set").as_deref(), Ok(text));
        }
        for text in ["con.5", "AUX.txt", "Props", "lpt1.2"] {
            assert!(check_id(&StdText, text, "set").is_err(), "{text}");
        }
    }

    #[test]
    fn versions_take_plus_and_refuse_underscores() {
        let rule = IdRule::Version;
        assert!(check_normalized(&StdText, rule, "1.0.0+build.5", "v").is_ok());
        for text in ["1..2", "+1", "1+", "1_2", "all", "con"] {
            assert!(
                check_normalized(&StdText, rule, text, "v").is_err(),
                "{text}"
            );
        }
    }

    #[test]
    fn case_clashes_are_sorted_pairs() {
        let pairs = find_case_clashes(&StdText, ["T1", "t1", "B2", "T1"]);
        assert_eq!(pairs, [("T1".to_owned(), "t1".to_owned())]);
        assert!(find_case_clashes(&StdText, ["a", "b"]).is_empty());
    }
}

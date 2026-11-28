//! json_scan --- reads JSON text left to right with the grammar and number rules of CPython's
//! `json` module, stopping at the first refusal
//!
//! Contains:
//!     Scanner: reads one JSON document, recording lone surrogates
//!     Scanner.document(): reads the whole text as one value with only whitespace around it

use std::collections::HashMap;

use super::canonical::{Json, MAX_NESTING};
use super::strict_json::{Invalid, ParseFailure, strict_error};

const SMALL_OBJECT: usize = 16;
const I64_DIGITS: usize = 18;
const HEX_LENGTH: usize = 4;
const HIGH_SURROGATES: std::ops::Range<u32> = 0xd800..0xdc00;
const LOW_SURROGATES: std::ops::Range<u32> = 0xdc00..0xe000;
const PAIR_BASE: u32 = 0x1_0000;
const PAIR_SHIFT: u32 = 10;

/// Reads one JSON document, stopping at the first refusal in reading order.
///
/// A NaN or infinity token, an over-long int and text that breaks the grammar stop the scan
/// where they are read; a repeated key stops it once its object closes. A lone surrogate is
/// only recorded, since standard JSON accepts it and the strict refusal comes last.
pub struct Scanner<'a> {
    source: &'a str,
    text: &'a [u8],
    at: usize,
    document: &'a str,
    int_digit_limit: usize,
    /// Whether any string or key held a lone surrogate escape.
    pub saw_lone_surrogate: bool,
}

/// A string as read: its text, with U+FFFD for each lone surrogate, and its exact generalized
/// UTF-8 bytes once a lone surrogate made them differ.
struct ScannedText {
    text: String,
    exact: Option<Vec<u8>>,
}

impl ScannedText {
    fn push_str(&mut self, part: &str) {
        self.text.push_str(part);
        if let Some(exact) = &mut self.exact {
            exact.extend_from_slice(part.as_bytes());
        }
    }

    fn push(&mut self, c: char) {
        let mut buffer = [0_u8; 4];
        self.push_str(c.encode_utf8(&mut buffer));
    }

    /// Appends a lone surrogate: U+FFFD to the text, its three-byte form to the exact bytes.
    fn push_surrogate(&mut self, unit: u32) {
        let exact = self
            .exact
            .get_or_insert_with(|| self.text.as_bytes().to_vec());
        for (shift, mark) in [(12, 0xe0), (6, 0x80), (0, 0x80)] {
            exact.push(u8::try_from(mark | ((unit >> shift) & 0x3f)).unwrap_or(0xbf));
        }
        self.text.push(char::REPLACEMENT_CHARACTER);
    }
}

impl<'a> Scanner<'a> {
    /// Creates a scanner over UTF-8 text; `int_digit_limit` 0 converts ints of any length.
    #[must_use]
    pub const fn new(source: &'a str, document: &'a str, int_digit_limit: usize) -> Self {
        Self {
            source,
            text: source.as_bytes(),
            at: 0,
            document,
            int_digit_limit,
            saw_lone_surrogate: false,
        }
    }

    /// Reads the whole text as one value with only JSON whitespace around it.
    ///
    /// # Errors
    ///
    /// Returns the first refusal in reading order.
    pub fn document(&mut self) -> Result<Json, ParseFailure> {
        self.skip_space();
        let value = self.value(0)?;
        self.skip_space();
        if self.at < self.text.len() {
            return Err(self.syntax());
        }
        Ok(value)
    }

    const fn syntax(&self) -> ParseFailure {
        ParseFailure::Invalid(Invalid::Syntax(self.at))
    }

    fn peek(&self) -> Option<u8> {
        self.text.get(self.at).copied()
    }

    fn is_digit(&self, index: usize) -> bool {
        self.text.get(index).is_some_and(u8::is_ascii_digit)
    }

    fn skip_space(&mut self) {
        while matches!(self.peek(), Some(b' ' | b'\t' | b'\n' | b'\r')) {
            self.at += 1;
        }
    }

    fn rest_starts_with(&self, word: &str) -> bool {
        self.text
            .get(self.at..)
            .is_some_and(|rest| rest.starts_with(word.as_bytes()))
    }

    fn value(&mut self, depth: usize) -> Result<Json, ParseFailure> {
        match self.peek() {
            Some(b'"') => {
                self.at += 1;
                Ok(Json::Text(self.string()?.text))
            }
            Some(b'{') => self.object(depth + 1),
            Some(b'[') => self.array(depth + 1),
            Some(b'n') => self.word("null", Json::Null),
            Some(b't') => self.word("true", Json::Bool(true)),
            Some(b'f') => self.word("false", Json::Bool(false)),
            Some(b'N') => self.refused_token("NaN"),
            Some(b'I') => self.refused_token("Infinity"),
            Some(b'-') if self.rest_starts_with("-Infinity") => self.refused_token("-Infinity"),
            _ => self.number(),
        }
    }

    fn word(&mut self, word: &str, value: Json) -> Result<Json, ParseFailure> {
        if !self.rest_starts_with(word) {
            return Err(self.syntax());
        }
        self.at += word.len();
        Ok(value)
    }

    /// Refuses NaN, Infinity and -Infinity where CPython's `parse_constant` hook runs.
    fn refused_token(&self, token: &str) -> Result<Json, ParseFailure> {
        if !self.rest_starts_with(token) {
            return Err(self.syntax());
        }
        let problem = format!("non-standard token {token}");
        Err(ParseFailure::Refused(
            strict_error(self.document, &problem).with_context("token", token),
        ))
    }

    /// Reads a number as CPython does: an int part, then a fraction only when a digit follows
    /// the point, then an exponent only when a digit follows `e` and its sign.
    fn number(&mut self) -> Result<Json, ParseFailure> {
        let start = self.at;
        let mut end = start;
        if self.text.get(end) == Some(&b'-') {
            end += 1;
        }
        let first_digit = end;
        match self.text.get(end) {
            Some(b'1'..=b'9') => {
                end += 1;
                while self.is_digit(end) {
                    end += 1;
                }
            }
            Some(b'0') => end += 1,
            _ => return Err(self.syntax()),
        }
        let digits = end - first_digit;
        let mut is_float = false;
        if self.text.get(end) == Some(&b'.') && self.is_digit(end + 1) {
            is_float = true;
            end += 2;
            while self.is_digit(end) {
                end += 1;
            }
        }
        if matches!(self.text.get(end), Some(b'e' | b'E')) {
            let mut exponent = end + 1;
            if matches!(self.text.get(exponent), Some(b'-' | b'+')) {
                exponent += 1;
            }
            if self.is_digit(exponent) {
                is_float = true;
                end = exponent;
                while self.is_digit(end) {
                    end += 1;
                }
            }
        }
        let token = self.source.get(start..end).ok_or_else(|| self.syntax())?;
        self.at = end;
        if is_float {
            return token
                .parse::<f64>()
                .map(Json::Float)
                .map_err(|_| ParseFailure::Invalid(Invalid::Syntax(start)));
        }
        if self.int_digit_limit > 0 && digits > self.int_digit_limit {
            return Err(ParseFailure::Invalid(Invalid::IntDigits(start)));
        }
        if digits <= I64_DIGITS
            && let Ok(number) = token.parse::<i64>()
        {
            return Ok(Json::Int(number));
        }
        Ok(Json::BigInt(token.to_owned()))
    }

    /// Reads a string whose opening quote is already consumed, through its closing quote.
    fn string(&mut self) -> Result<ScannedText, ParseFailure> {
        let mut read = ScannedText {
            text: String::new(),
            exact: None,
        };
        let mut chunk = self.at;
        loop {
            match self.peek() {
                Some(b'"') => {
                    read.push_str(
                        self.source
                            .get(chunk..self.at)
                            .ok_or_else(|| self.syntax())?,
                    );
                    self.at += 1;
                    return Ok(read);
                }
                Some(b'\\') => {
                    read.push_str(
                        self.source
                            .get(chunk..self.at)
                            .ok_or_else(|| self.syntax())?,
                    );
                    self.at += 1;
                    self.escape(&mut read)?;
                    chunk = self.at;
                }
                Some(0x00..=0x1f) | None => return Err(self.syntax()),
                Some(_) => self.at += 1,
            }
        }
    }

    /// Reads one escape after its backslash, joining a surrogate pair written as two escapes.
    fn escape(&mut self, read: &mut ScannedText) -> Result<(), ParseFailure> {
        let simple = match self.peek() {
            Some(b'"') => '"',
            Some(b'\\') => '\\',
            Some(b'/') => '/',
            Some(b'b') => '\u{8}',
            Some(b'f') => '\u{c}',
            Some(b'n') => '\n',
            Some(b'r') => '\r',
            Some(b't') => '\t',
            Some(b'u') => return self.unicode_escape(read),
            _ => return Err(self.syntax()),
        };
        self.at += 1;
        read.push(simple);
        Ok(())
    }

    fn unicode_escape(&mut self, read: &mut ScannedText) -> Result<(), ParseFailure> {
        let unit = self.hex(self.at + 1).ok_or_else(|| self.syntax())?;
        self.at += 1 + HEX_LENGTH;
        if HIGH_SURROGATES.contains(&unit) {
            let low = (self.text.get(self.at..self.at + 2) == Some(b"\\u"))
                .then(|| self.hex(self.at + 2))
                .flatten()
                .filter(|low| LOW_SURROGATES.contains(low));
            if let Some(low) = low {
                self.at += 2 + HEX_LENGTH;
                let joined = PAIR_BASE
                    + ((unit - HIGH_SURROGATES.start) << PAIR_SHIFT)
                    + (low - LOW_SURROGATES.start);
                read.push(char::from_u32(joined).unwrap_or(char::REPLACEMENT_CHARACTER));
                return Ok(());
            }
        }
        match char::from_u32(unit) {
            Some(c) => read.push(c),
            None => {
                self.saw_lone_surrogate = true;
                read.push_surrogate(unit);
            }
        }
        Ok(())
    }

    fn hex(&self, at: usize) -> Option<u32> {
        let digits = self.source.get(at..at + HEX_LENGTH)?;
        if !digits.bytes().all(|byte| byte.is_ascii_hexdigit()) {
            return None;
        }
        u32::from_str_radix(digits, 16).ok()
    }

    fn object(&mut self, depth: usize) -> Result<Json, ParseFailure> {
        if depth > MAX_NESTING {
            return Err(self.syntax());
        }
        self.at += 1;
        self.skip_space();
        let mut members: Vec<(String, Json)> = Vec::new();
        let mut exact_keys = Vec::new();
        if self.peek() == Some(b'}') {
            self.at += 1;
            return Ok(Json::Object(members));
        }
        loop {
            if self.peek() != Some(b'"') {
                return Err(self.syntax());
            }
            self.at += 1;
            let key = self.string()?;
            self.skip_space();
            if self.peek() != Some(b':') {
                return Err(self.syntax());
            }
            self.at += 1;
            self.skip_space();
            let value = self.value(depth)?;
            exact_keys.push(key.exact);
            members.push((key.text, value));
            if self.after_member(b'}')? {
                break;
            }
        }
        let keys: Vec<&[u8]> = members
            .iter()
            .zip(&exact_keys)
            .map(|((text, _), exact)| exact.as_deref().unwrap_or(text.as_bytes()))
            .collect();
        if let Some(repeated) = first_repeated(&keys) {
            return Err(ParseFailure::RepeatedKey(repeated.to_vec()));
        }
        Ok(Json::Object(members))
    }

    fn array(&mut self, depth: usize) -> Result<Json, ParseFailure> {
        if depth > MAX_NESTING {
            return Err(self.syntax());
        }
        self.at += 1;
        self.skip_space();
        let mut items = Vec::new();
        if self.peek() == Some(b']') {
            self.at += 1;
            return Ok(Json::Array(items));
        }
        loop {
            items.push(self.value(depth)?);
            if self.after_member(b']')? {
                return Ok(Json::Array(items));
            }
        }
    }

    /// Reads what follows a member: the closing bracket (true) or a comma that another member
    /// follows (false); a comma before the closing bracket breaks the grammar.
    fn after_member(&mut self, close: u8) -> Result<bool, ParseFailure> {
        self.skip_space();
        match self.peek() {
            Some(byte) if byte == close => {
                self.at += 1;
                Ok(true)
            }
            Some(b',') => {
                self.at += 1;
                self.skip_space();
                if self.peek() == Some(close) {
                    return Err(self.syntax());
                }
                Ok(false)
            }
            _ => Err(self.syntax()),
        }
    }
}

/// Returns the first key, in order of first appearance, that appears more than once.
///
/// Small objects compare keys pairwise and larger ones sort them, so no key is hashed unless
/// one repeats.
fn first_repeated<'k>(keys: &[&'k [u8]]) -> Option<&'k [u8]> {
    let repeats = if keys.len() <= SMALL_OBJECT {
        keys.iter()
            .enumerate()
            .any(|(index, key)| keys.iter().skip(index + 1).any(|other| other == key))
    } else {
        let mut sorted = keys.to_vec();
        sorted.sort_unstable();
        sorted.windows(2).any(|pair| pair[0] == pair[1])
    };
    if !repeats {
        return None;
    }
    let mut counts: HashMap<&[u8], usize> = HashMap::with_capacity(keys.len());
    for key in keys {
        *counts.entry(key).or_insert(0) += 1;
    }
    keys.iter()
        .find(|key| counts.get(**key).is_some_and(|count| *count > 1))
        .copied()
}

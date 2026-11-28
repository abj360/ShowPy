//! hashing --- SHA-256 content hashes under a domain tag, and asset URIs
//!
//! Contains:
//!     HASH_PREFIX: the frozen tag that opens every hashed header
//!     DEFAULT_DOMAIN: the domain content_hash uses when none is given
//!     ASSET_PREFIX: the scheme of every asset URI
//!     HashKind: names what a hashed payload holds: bytes, text or canonical JSON
//!     check_domain(): rejects a hash domain that is not lowercase ASCII letters, digits, _ and .
//!     domain_refused(): builds the error for a refused hash domain
//!     content_hash(): hashes a payload under a domain and kind tag
//!     asset_uri(): names bytes by the `asset:sha256:<hex>` URI an asset store returns

use sha2::{Digest, Sha256};

use super::error::{CoreError, CoreResult};
use super::error_codes::{ErrorCode, ErrorKind};
use super::text::{TextHost, str_repr};

/// The frozen tag that opens every hashed header.
pub const HASH_PREFIX: &str = "showpy-hash/1";
/// The domain content_hash uses when none is given.
pub const DEFAULT_DOMAIN: &str = "showpy.content";
/// The scheme of every asset URI.
pub const ASSET_PREFIX: &str = "asset:sha256:";
/// Caps the characters of a refused domain an error repeats.
const SHOWN_DOMAIN_CHARS: usize = 80;
const HEX_DIGITS: &[u8; 16] = b"0123456789abcdef";

/// Names what a hashed payload holds, so equal bytes of different kinds never collide.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum HashKind {
    /// Raw bytes, hashed as themselves.
    Bytes,
    /// Text, hashed as its UTF-8 bytes.
    Str,
    /// Any other value, hashed as its RFC 8785 canonical JSON bytes.
    Json,
}

impl HashKind {
    const fn tag(self) -> &'static str {
        match self {
            Self::Bytes => "bytes",
            Self::Str => "str",
            Self::Json => "json",
        }
    }
}

/// Rejects a hash domain that is not one or more lowercase ASCII letters, digits, `_` and `.`.
///
/// The domain is one line of the hash header, so it may not hold a newline or forge a kind
/// line.
///
/// # Errors
///
/// Returns a SchemaError that shows the domain's first 80 characters as Python's `repr`; the
/// host writes non-ASCII text.
pub fn check_domain(host: &dyn TextHost, domain: &str) -> CoreResult<()> {
    let allowed =
        |byte: &u8| byte.is_ascii_lowercase() || byte.is_ascii_digit() || b"_.".contains(byte);
    if !domain.is_empty() && domain.as_bytes().iter().all(allowed) {
        return Ok(());
    }
    let cut: String = domain.chars().take(SHOWN_DOMAIN_CHARS).collect();
    Err(domain_refused(&str_repr(host, &cut)?, &cut))
}

/// Builds the error for a refused hash domain from its first 80 characters, as `repr` shows
/// them and as they are.
#[must_use]
pub fn domain_refused(shown: &str, cut: &str) -> CoreError {
    CoreError::new(
        ErrorKind::Schema,
        ErrorCode::Schema,
        format!("hash domain {shown} is not allowed"),
        "use lowercase ASCII letters, digits, _ and . only, as in showpy.revision",
    )
    .with_context("domain", cut)
}

/// Hashes a payload as SHA-256 over `showpy-hash/1\n<domain>\n<kind>\n` and the payload.
///
/// The domain must already have passed `check_domain`.
///
/// Returns:
/// digest: 64 lowercase hex characters.
#[must_use]
pub fn content_hash(domain: &str, kind: HashKind, payload: &[u8]) -> String {
    let mut hasher = Sha256::new();
    for part in [HASH_PREFIX, "\n", domain, "\n", kind.tag(), "\n"] {
        hasher.update(part.as_bytes());
    }
    hasher.update(payload);
    hex(&hasher.finalize())
}

/// Names bytes by the `asset:sha256:<hex>` URI an asset store returns for them.
///
/// Returns:
/// uri: `asset:sha256:` followed by the SHA-256 hex digest of `data`.
#[must_use]
pub fn asset_uri(data: &[u8]) -> String {
    format!("{ASSET_PREFIX}{}", hex(&Sha256::digest(data)))
}

fn hex(bytes: &[u8]) -> String {
    let mut out = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        out.push(char::from(HEX_DIGITS[usize::from(byte >> 4)]));
        out.push(char::from(HEX_DIGITS[usize::from(byte & 0x0f)]));
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::base::text::StdText;

    #[test]
    fn asset_uri_of_no_bytes_is_the_empty_sha256() {
        assert_eq!(
            asset_uri(b""),
            "asset:sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        );
    }

    #[test]
    fn content_hash_tags_the_domain_and_the_kind() {
        let digest = content_hash(DEFAULT_DOMAIN, HashKind::Json, b"{\"a\":1}");
        assert_eq!(digest.len(), 64);
        assert_ne!(
            digest,
            content_hash("showpy.revision", HashKind::Json, b"{\"a\":1}")
        );
        assert_ne!(
            digest,
            content_hash(DEFAULT_DOMAIN, HashKind::Str, b"{\"a\":1}")
        );
        assert_eq!(
            content_hash(DEFAULT_DOMAIN, HashKind::Bytes, b""),
            hex(&Sha256::digest(b"showpy-hash/1\nshowpy.content\nbytes\n"))
        );
    }

    #[test]
    fn domains_take_lowercase_ascii_letters_digits_underscores_and_dots() {
        assert_eq!(check_domain(&StdText, "showpy.revision_2"), Ok(()));
        for refused in ["", "Show", "a b", "a-b"] {
            assert!(check_domain(&StdText, refused).is_err(), "{refused}");
        }
        let error = check_domain(&StdText, "it's").err();
        assert_eq!(
            error.as_ref().map(CoreError::message),
            Some("hash domain \"it's\" is not allowed")
        );
    }
}

# ADR 0018: Stored publications hold no join token

- Status: Accepted (2026-11-16)
- Date: 2026-11-16
- Deciders: the maintainer (abj360)

## Context

Task E1 of Phase 1 closed UP-9.5-01 (a publication link anyone could guess) with a join token.
`publish(..., join_token=T)` builds `Publication.link` as `<base_url>/<publication id>?t=<T>` and
stores `join_token_sha256 = sha256(T)` next to it; `verify_join_token` checks a presented token
against that digest in constant time.

The lane E security review (finding I1, 2026-11-15) showed the hash protects nothing at rest:

- **At-rest risk.** `Publication` is the record hosts persist. `link` carries the raw token, so
  anyone who reads a stored or exported publication (a database dump, a backup, a JSON export, a
  log of `model_dump_json()`) holds a working join link for that show. E1 hid `link` from
  `repr` (ruling R10) but kept it in every dump.
- **Republish trap.** `publish(previous=...)` required the raw token again on every republish,
  so a host that wanted to keep the same link had to persist the token somewhere it could read
  back, which is the leak the hash was meant to prevent.

The maintainer chose on 2026-11-16 to fix this in the release that already breaks `publish`
(v0.3.0), so consumers migrate once.

## Decision

1. **`Publication.link` holds no secret.** It is the join URL without the token,
   `<base_url>/<publication id>`, and `Publication` stores only `join_token_sha256`. Nothing
   ShowPy persists, exports or renders contains the raw token: `model_dump`, `export_json`,
   print sheets, `repr`, error messages and error contexts.
2. **`join_link(pub, token) -> str` builds the joinable link,** and `qr_code` builds it through
   `join_link`. It verifies the token against `pub.join_token_sha256` with `verify_join_token`
   (constant time) and returns `<pub.link>?t=<token>`, the link format E1 used. A malformed
   token raises `SchemaError`; a wrong token, a publication that stores no digest, or a stored
   link that already has a query or fragment raises `ValidationError`. It fails closed and
   never echoes the token.
3. **`qr_code(pub, token, size=512)` takes the token,** verifies it the same way and encodes
   `join_link(pub, token)`. Its size caps and error level are unchanged. `performer_view` keeps
   building its animation URL from the tokenless `link`; print sheets, charts and
   `notify_changes` never read the link.
4. **`publish(show, publication_id, base_url, at, previous=None, *, join_token=None)`.** The
   first publish (`previous is None`) requires `join_token` (22 to 256 characters of
   `[A-Za-z0-9_-]`, as in E1). A republish with `join_token=None` keeps
   `previous.join_token_sha256`; passing a token rotates the digest. A republish of a
   publication that stores no digest (v0.2.0) requires a token, since there is nothing to keep.
5. **E1-era records load tokenless.** A stored `Publication` whose `link` carries `?t=<token>`
   loses the `t` parameter (any case, in the query or in a query inside the fragment) when it
   is validated, before any field is checked, so neither the loaded model nor a validation
   error echoes it. A `link` that is not text (bytes, a list) is refused with a `SchemaError`
   that names only its type, since pydantic would coerce bytes after the strip or echo the
   value. The digest is kept, so the issued token still joins. `model_copy(update=)` skips
   validators, so it is a host-side gap; `replace` strips.

## Alternatives

- **Keep the token in `link` and document `Publication` as a secret record.** Rejected by the
  maintainer on 2026-11-16: every host would have to encrypt or redact one field of an
  otherwise public record, and one that forgets leaks every show's join link.
- **HMAC-signed links (`?t=<id>.<signature>`).** Rejected: the host would have to give ShowPy a
  signing key, and a pure library should not hold or rotate secrets. Revoking one link would also
  need a key rotation that revokes every link signed with that key.
- **Return the joinable link from `publish` in a separate, non-persisted value.** Rejected:
  `publish` returns one `Publication` everywhere else in the API, and the host still needs the
  link again later (a reprinted QR code); `join_link` serves both moments.

## Consequences

- Breaking, in v0.3.0 next to E1's changes: `Publication.link` loses `?t=<token>`; `qr_code`
  gains a required `token` argument; `publish`'s `join_token` is optional on a republish; new
  public name `join_link`.
- Hosts deliver the token to performers themselves: print or send `join_link(pub, token)`, or a
  `qr_code(pub, token)`, at the moment they hold the token, and keep at most the publication.
- A host that loses the token cannot rebuild the joinable link; it republishes with a new token,
  which also revokes the old link.
- `verify_join_token` stays public and unchanged for hosts that check a token presented by a
  performer.

## Numbers

| Measure                                                | E1 design        | After |
| ------------------------------------------------------ | ---------------- | ----- |
| Raw token copies in `Publication.model_dump_json()`    | 1 (`link`)       | 0     |
| Raw token copies in `repr(Publication)`                | 0 (`repr=False`) | 0     |
| Republishes that need the raw token                    | every one        | 0     |
| Token length accepted (characters of `[A-Za-z0-9_-]`)  | 22 to 256        | same  |
| Entropy of `secrets.token_urlsafe(16)` (22 characters) | 128 bits         | same  |
| Public names changed or added                          | —                | 4     |

## What would change our mind

- A host that must reprint a joinable link with no access to the token, for example a kiosk
  rebuilt from a backup: then a host-keyed signed link (the HMAC alternative) is reconsidered,
  with the key held by the host, not ShowPy.
- Evidence that hosts persist `join_link` output anyway: then `join_link` returns a value whose
  `repr` and serialization hide the token.

## References

- Audit `docs/upgrade-plan.md` section 9.5, ledger ID UP-9.5-01 (guessable publication link).
- Phase 1 task E1 (`publish(..., join_token=)`, `verify_join_token`; commit `5d6fe52`).
- Lane E security review of E1, finding I1 (raw token beside its hash), and ruling R10.
- Code: `showpy/export/publish.py`, `showpy/export/models.py` (`Publication`).

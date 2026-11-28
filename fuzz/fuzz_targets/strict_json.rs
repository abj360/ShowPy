//! strict_json.rs --- fuzz target for the strict JSON reader, the nesting count and the
//! canonical writer: parsed text written canonically reads back to the same bytes

#![no_main]

use e8s_core::base::canonical::{Json, encoded};
use e8s_core::base::strict_json::{json_nesting, parse_strict_json};
use e8s_core::base::text::StdText;
use libfuzzer_sys::fuzz_target;

const DOCUMENT: &str = "fuzz.json";
const INT_DIGIT_LIMIT: usize = 4300;

fn all_finite(value: &Json) -> bool {
    match value {
        Json::Float(number) => number.is_finite(),
        Json::Array(items) => items.iter().all(all_finite),
        Json::Object(members) => members.iter().all(|(_, item)| all_finite(item)),
        _ => true,
    }
}

fuzz_target!(|data: &[u8]| {
    let _ = json_nesting(data);
    let Ok(value) = parse_strict_json(data, DOCUMENT, INT_DIGIT_LIMIT, &StdText) else {
        return;
    };
    if !all_finite(&value) {
        return;
    }
    let written = encoded(&value);
    let reread = parse_strict_json(&written, DOCUMENT, 0, &StdText).map(|again| encoded(&again));
    assert_eq!(reread, Ok(written));
});

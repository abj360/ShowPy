//! refs.rs --- fuzz target for the `kind:id@version` reference and transition ID parsers and the
//! ID checks

#![no_main]

use e8s_core::base::ids::{check_id, check_sheet_id, find_case_clashes, is_device_name};
use e8s_core::base::refs::{format_ref, parse_ref, split_tid};
use e8s_core::base::text::StdText;
use libfuzzer_sys::fuzz_target;

const KIND: &str = "fuzz";

fuzz_target!(|data: &[u8]| {
    let Ok(text) = std::str::from_utf8(data) else {
        return;
    };
    let _ = parse_ref(&StdText, text);
    let _ = split_tid(&StdText, text);
    let _ = check_id(&StdText, text, KIND);
    let _ = check_sheet_id(&StdText, text, KIND);
    let _ = is_device_name(&StdText, text);
    let _ = format_ref(&StdText, KIND, text, Some(text));
    let _ = find_case_clashes(&StdText, text.split(':'));
});

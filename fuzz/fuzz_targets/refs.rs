//! refs.rs --- fuzz target for the `kind:id@version` reference and transition ID parsers, the ID
//! checks, ID ranges and natural sorting, close-name suggestions and the CSV formula guard

#![no_main]

use e8s_core::base::csv_cells::needs_guard;
use e8s_core::base::ids::{check_id, check_sheet_id, find_case_clashes, is_device_name};
use e8s_core::base::natural::natural_order;
use e8s_core::base::ranges::{format_ranges, parse_range, split_id};
use e8s_core::base::refs::{format_ref, parse_ref, split_tid};
use e8s_core::base::similar::close_matches;
use e8s_core::base::text::StdText;
use e8s_core::base::text_bytes::code_points;
use libfuzzer_sys::fuzz_target;

const KIND: &str = "fuzz";
const SEPARATOR: u8 = b',';

fuzz_target!(|data: &[u8]| {
    let pieces: Vec<&[u8]> = data.split(|&byte| byte == SEPARATOR).collect();
    let _ = split_id(&StdText, data);
    if let Ok(range) = parse_range(&StdText, data) {
        let _ = range.for_each_id(|_| Ok::<(), ()>(()));
    }
    let _ = format_ranges(&StdText, &pieces);
    let _ = natural_order(&pieces);
    let _ = needs_guard(data);
    let pool: Vec<Vec<u32>> = pieces.iter().map(|piece| code_points(piece)).collect();
    let _ = close_matches(&code_points(data), &pool);
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

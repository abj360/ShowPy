//! strict_json.rs --- fuzz target for the strict JSON reader; empty until its lane fills it

#![no_main]

use libfuzzer_sys::fuzz_target;

// Linked so the target builds against the core it will fuzz.
use e8s_core as _;

fuzz_target!(|_data: &[u8]| {});

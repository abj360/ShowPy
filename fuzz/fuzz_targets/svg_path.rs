//! svg_path.rs --- fuzz target for the SVG path data parser; empty until its lane fills it

#![no_main]

use libfuzzer_sys::fuzz_target;

// Linked so the target builds against the core it will fuzz.
use e8s_core as _;

fuzz_target!(|_data: &[u8]| {});

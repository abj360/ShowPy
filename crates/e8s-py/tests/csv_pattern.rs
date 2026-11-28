//! csv_pattern --- holds the hand-written plain-decimal matcher to the pattern text it states
//!
//! Contains:
//!     plain_decimal_matcher_agrees_with_its_pattern_text: compiles PLAIN_DECIMAL with Python's
//!         `re` (ASCII) and compares `fullmatch` with `is_plain_decimal` on every string up to
//!         five characters over the pattern's alphabet, plus hand-picked cells

use e8s_core::base::csv_cells::{PLAIN_DECIMAL, is_plain_decimal};
use pyo3::prelude::*;

/// The characters the pattern reads, one it never reads, and a non-ASCII digit.
const ALPHABET: [&str; 8] = ["+", "-", ".", "e", "E", "0", "x", "\u{661}"];
const LONGEST: usize = 5;
const HAND_PICKED: [&str; 8] = [
    "-0005.000E-0003",
    "+3.5e10",
    "1.e5",
    "+5\n",
    "\t5",
    "12345678901234567890",
    ".5e+1",
    "5e",
];

fn every_string() -> Vec<String> {
    let mut all = vec![String::new()];
    let mut layer = vec![String::new()];
    for _ in 0..LONGEST {
        layer = layer
            .iter()
            .flat_map(|stem| ALPHABET.iter().map(move |c| format!("{stem}{c}")))
            .collect();
        all.extend(layer.iter().cloned());
    }
    all.extend(HAND_PICKED.iter().map(|cell| (*cell).to_owned()));
    all
}

#[test]
fn plain_decimal_matcher_agrees_with_its_pattern_text() -> PyResult<()> {
    Python::attach(|py| {
        let re = py.import("re")?;
        let pattern = re.call_method1("compile", (PLAIN_DECIMAL, re.getattr("ASCII")?))?;
        let mut disagreements = Vec::new();
        for cell in every_string() {
            let by_pattern = !pattern.call_method1("fullmatch", (&cell,))?.is_none();
            if by_pattern != is_plain_decimal(cell.as_bytes()) {
                disagreements.push(cell);
            }
        }
        assert_eq!(disagreements, Vec::<String>::new());
        Ok(())
    })
}

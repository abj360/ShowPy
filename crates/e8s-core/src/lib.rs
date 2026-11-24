//! e8s-core --- the compiled engine of e8s: pure Rust, one module per Python package
//!
//! Contains:
//!     base: units, identifiers, errors and canonical encodings (the `e8s.core` package)
//!     geo: curves, shapes, regions, spacing and transforms (`e8s.geo`)
//!     time: the count timeline and its clock (`e8s.time`)
//!     sheet: sheet templates, coordinate systems and bounds (`e8s.sheet`)
//!     cast: spots, groups and the selection grammar (`e8s.cast`)
//!     choreo: the path engine, forms and matching (`e8s.choreo`)
//!     assign: the assignment solvers that matching uses
//!     rng: the seeded random generator

#![forbid(unsafe_code)]

// Linked before the first port calls it, so the lock pins the one transcendental source.
use libm as _;

pub mod assign;
pub mod base;
pub mod cast;
pub mod choreo;
pub mod geo;
pub mod rng;
pub mod sheet;
pub mod time;

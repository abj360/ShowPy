//! rng --- the seeded random generator behind `seeded_rng`, numpy's PCG64 draw for draw
//!
//! Contains:
//!     pcg64: the PCG64 bit generator, its floats and its bounded integers
//!     sample: sampling without replacement
//!     seed_sequence: numpy's seed hashing
//!     Pcg64: the generator a seed starts

pub mod pcg64;
pub mod sample;
pub mod seed_sequence;

pub use pcg64::Pcg64;

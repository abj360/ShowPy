//! pcg64 --- numpy's PCG64 bit generator and the bounded draws of `numpy.random.Generator`
//!
//! The state, the XSL-RR output, the half-word buffer of 32-bit draws, the 53-bit floats and
//! Lemire's bounded integers are numpy's, so a generator seeded alike draws numpy's values.
//!
//! Contains:
//!     Pcg64: a PCG64 generator and its pending 32-bit half-word
//!     Pcg64::from_seed(): seeds the generator as `PCG64(SeedSequence(seed))` does
//!     Pcg64::next_u64(): draws 64 random bits
//!     Pcg64::next_u32(): draws 32 random bits, using the kept half of a 64-bit draw first
//!     Pcg64::random(): draws a float in [0, 1) as `Generator.random()` does
//!     Pcg64::bounded(): draws an integer in [0, rng], inclusive, as numpy's bounded draws do
//!     Pcg64::integers(): draws an integer in [0, high) as `Generator.integers(high)` does

use super::seed_sequence::SeedSequence;
use crate::base::{CoreError, CoreResult, ErrorCode, ErrorKind};

const MULTIPLIER: u128 = 0x2360_ed05_1fc6_5da4_4385_df64_9fcc_f645;
const STATE_WORDS: usize = 4;
const HALF_BITS: u32 = 64;
const ROTATION_SHIFT: u32 = 122;
const FLOAT_SHIFT: u32 = 11;
const FLOAT_SCALE: f64 = 1.0 / 9_007_199_254_740_992.0;
const LOW_WORD: u64 = 0xffff_ffff;

/// Holds a PCG64 generator: its 128-bit state and increment, and the high half of the last
/// 64-bit draw that a 32-bit draw has not yet used.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Pcg64 {
    state: u128,
    inc: u128,
    has_uint32: bool,
    uinteger: u32,
}

impl Pcg64 {
    /// Seeds a generator as numpy's `PCG64(SeedSequence(seed))` does.
    ///
    /// The seed sequence's first two words seed the state and the last two the stream.
    #[must_use]
    pub fn from_seed(seed: u64) -> Self {
        let words = SeedSequence::from_seed(seed).generate_state_u64(STATE_WORDS);
        let joined = |high: usize, low: usize| {
            words
                .get(high)
                .map_or(0, |&word| u128::from(word) << HALF_BITS)
                | words.get(low).map_or(0, |&word| u128::from(word))
        };
        let (initstate, initseq) = (joined(0, 1), joined(2, 3));
        let mut generator = Self {
            state: 0,
            inc: (initseq << 1) | 1,
            has_uint32: false,
            uinteger: 0,
        };
        generator.step();
        generator.state = generator.state.wrapping_add(initstate);
        generator.step();
        generator
    }

    fn step(&mut self) {
        self.state = self.state.wrapping_mul(MULTIPLIER).wrapping_add(self.inc);
    }

    /// Draws 64 random bits: steps the state, then folds and rotates it (XSL-RR).
    pub fn next_u64(&mut self) -> u64 {
        self.step();
        let folded = ((self.state >> HALF_BITS) as u64) ^ (self.state as u64);
        folded.rotate_right((self.state >> ROTATION_SHIFT) as u32)
    }

    /// Draws 32 random bits: the kept high half of the last 64-bit draw, else the low half of a
    /// new draw, keeping its high half for the next call.
    pub fn next_u32(&mut self) -> u32 {
        if self.has_uint32 {
            self.has_uint32 = false;
            return self.uinteger;
        }
        let next = self.next_u64();
        self.has_uint32 = true;
        self.uinteger = (next >> 32) as u32;
        (next & LOW_WORD) as u32
    }

    /// Draws a float in [0, 1) from the top 53 bits of a 64-bit draw, as `Generator.random()`.
    pub fn random(&mut self) -> f64 {
        (self.next_u64() >> FLOAT_SHIFT) as f64 * FLOAT_SCALE
    }

    /// Draws an integer in [0, rng], both ends included, as numpy's `random_bounded_uint64`
    /// does without a mask: none for 0, 32-bit Lemire up to `u32::MAX - 1`, a raw 32-bit draw
    /// at `u32::MAX`, 64-bit Lemire above it, and a raw 64-bit draw at `u64::MAX`.
    pub fn bounded(&mut self, rng: u64) -> u64 {
        if rng == 0 {
            return 0;
        }
        if rng == LOW_WORD {
            return u64::from(self.next_u32());
        }
        if rng < LOW_WORD {
            return u64::from(self.lemire_u32(rng as u32));
        }
        if rng == u64::MAX {
            return self.next_u64();
        }
        self.lemire_u64(rng)
    }

    fn lemire_u32(&mut self, rng: u32) -> u32 {
        let rng_excl = rng + 1;
        let mut product = u64::from(self.next_u32()) * u64::from(rng_excl);
        let mut leftover = product as u32;
        if leftover < rng_excl {
            let threshold = (u32::MAX - rng) % rng_excl;
            while leftover < threshold {
                product = u64::from(self.next_u32()) * u64::from(rng_excl);
                leftover = product as u32;
            }
        }
        (product >> 32) as u32
    }

    fn lemire_u64(&mut self, rng: u64) -> u64 {
        let rng_excl = rng + 1;
        let mut product = u128::from(self.next_u64()) * u128::from(rng_excl);
        let mut leftover = product as u64;
        if leftover < rng_excl {
            let threshold = (u64::MAX - rng) % rng_excl;
            while leftover < threshold {
                product = u128::from(self.next_u64()) * u128::from(rng_excl);
                leftover = product as u64;
            }
        }
        (product >> HALF_BITS) as u64
    }

    /// Draws an integer in [0, high) as `Generator.integers(high)` does.
    ///
    /// # Errors
    ///
    /// Returns a SchemaError for `high` 0, an empty range, where numpy raises ValueError.
    pub fn integers(&mut self, high: u64) -> CoreResult<u64> {
        if high == 0 {
            return Err(CoreError::new(
                ErrorKind::Schema,
                ErrorCode::Schema,
                "high must be at least 1, got 0",
                "draw from a range that holds at least one integer",
            )
            .with_context("high", 0_i64));
        }
        Ok(self.bounded(high - 1))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn seeding_matches_numpy_state_for_seed_zero() {
        // numpy: np.random.PCG64(0).state["state"]
        let generator = Pcg64::from_seed(0);
        assert_eq!(
            generator.state,
            35_399_562_948_360_463_058_890_781_895_381_311_971
        );
        assert_eq!(
            generator.inc,
            87_136_372_517_582_989_555_478_159_403_783_844_777
        );
    }

    #[test]
    fn raw_draws_and_floats_match_numpy() {
        // numpy: np.random.PCG64(0).random_raw(3)
        let mut generator = Pcg64::from_seed(0);
        let raw: Vec<u64> = (0..3).map(|_| generator.next_u64()).collect();
        assert_eq!(
            raw,
            vec![
                0xa30f_ebcf_d9c2_825f,
                0x4510_bdf8_82d9_d721,
                0x0a7d_3da9_4ecd_e8b8
            ]
        );
        // numpy: np.random.Generator(np.random.PCG64(42)).random(2)
        let mut seeded = Pcg64::from_seed(42);
        assert_eq!(
            (seeded.random(), seeded.random()),
            (0.773_956_048_555_963_3, 0.438_878_439_752_052_3)
        );
    }

    #[test]
    fn a_32_bit_draw_keeps_the_high_half_for_the_next() {
        let mut generator = Pcg64::from_seed(0);
        let (low, high) = (generator.next_u32(), generator.next_u32());
        assert_eq!(
            (u64::from(high) << 32) | u64::from(low),
            0xa30f_ebcf_d9c2_825f
        );
    }

    #[test]
    fn an_empty_range_is_refused() {
        let mut generator = Pcg64::from_seed(0);
        assert!(generator.integers(0).is_err());
        assert_eq!(generator.integers(1), Ok(0));
    }
}

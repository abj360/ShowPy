//! seed_sequence --- numpy's `SeedSequence` for one non-negative integer seed
//!
//! numpy hashes a seed into a pool of four 32-bit words and draws a generator's starting state
//! from that pool, so equal seeds give equal streams and nearby seeds give unrelated ones. The
//! constants and mixing steps are numpy's, so the words are numpy's bit for bit.
//!
//! Contains:
//!     SeedSequence: the mixed entropy pool of one seed
//!     SeedSequence::from_seed(): hashes a seed into the pool
//!     SeedSequence::generate_state_u64(): draws 64-bit words of starting state from the pool

const INIT_A: u32 = 0x43b0_d7e5;
const MULT_A: u32 = 0x931e_8875;
const INIT_B: u32 = 0x8b51_f9dd;
const MULT_B: u32 = 0x58f3_8ded;
const MIX_MULT_L: u32 = 0xca01_f9dd;
const MIX_MULT_R: u32 = 0x4973_f715;
const XSHIFT: u32 = 16;
const POOL_SIZE: usize = 4;
const WORD_BITS: u32 = 32;

/// Holds the mixed entropy pool of one seed, as numpy's `SeedSequence(seed)` does.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SeedSequence {
    pool: [u32; POOL_SIZE],
}

/// Hashes one entropy word with the running hash constant, advancing the constant.
fn hashmix(value: u32, hash_const: &mut u32) -> u32 {
    let mut mixed = value ^ *hash_const;
    *hash_const = hash_const.wrapping_mul(MULT_A);
    mixed = mixed.wrapping_mul(*hash_const);
    mixed ^ (mixed >> XSHIFT)
}

/// Mixes two words into one.
fn mix(x: u32, y: u32) -> u32 {
    let result = MIX_MULT_L
        .wrapping_mul(x)
        .wrapping_sub(MIX_MULT_R.wrapping_mul(y));
    result ^ (result >> XSHIFT)
}

/// Splits a seed into 32-bit words, least significant first; zero is one zero word.
fn entropy_words(seed: u64) -> Vec<u32> {
    let mut words = Vec::with_capacity(2);
    let mut rest = seed;
    loop {
        words.push((rest & u64::from(u32::MAX)) as u32);
        rest >>= WORD_BITS;
        if rest == 0 {
            return words;
        }
    }
}

impl SeedSequence {
    /// Hashes a seed into the entropy pool, as numpy's `SeedSequence(seed)` does.
    #[must_use]
    pub fn from_seed(seed: u64) -> Self {
        let entropy = entropy_words(seed);
        let mut hash_const = INIT_A;
        let mut pool = [0_u32; POOL_SIZE];
        for (index, slot) in pool.iter_mut().enumerate() {
            *slot = hashmix(entropy.get(index).copied().unwrap_or(0), &mut hash_const);
        }
        for source in 0..POOL_SIZE {
            for target in 0..POOL_SIZE {
                if source != target {
                    pool[target] = mix(pool[target], hashmix(pool[source], &mut hash_const));
                }
            }
        }
        for &word in entropy.iter().skip(POOL_SIZE) {
            for slot in &mut pool {
                *slot = mix(*slot, hashmix(word, &mut hash_const));
            }
        }
        Self { pool }
    }

    /// Draws `count` 64-bit words of starting state, as `generate_state(count, np.uint64)` does.
    ///
    /// Each word joins two 32-bit draws, the first as its low half.
    #[must_use]
    pub fn generate_state_u64(&self, count: usize) -> Vec<u64> {
        let mut hash_const = INIT_B;
        let halves: Vec<u32> = self
            .pool
            .iter()
            .cycle()
            .take(count * 2)
            .map(|&word| {
                let mut value = word ^ hash_const;
                hash_const = hash_const.wrapping_mul(MULT_B);
                value = value.wrapping_mul(hash_const);
                value ^ (value >> XSHIFT)
            })
            .collect();
        halves
            .as_chunks::<2>()
            .0
            .iter()
            .map(|&[low, high]| u64::from(low) | (u64::from(high) << WORD_BITS))
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn seed_zero_is_one_zero_word_and_large_seeds_split_low_first() {
        assert_eq!(entropy_words(0), vec![0]);
        assert_eq!(entropy_words(0x1_0000_0002), vec![2, 1]);
    }

    #[test]
    fn state_words_match_numpy_for_one_and_two_word_seeds() {
        // numpy: np.random.SeedSequence(seed).generate_state(4, np.uint64)
        assert_eq!(
            SeedSequence::from_seed(0).generate_state_u64(4),
            vec![
                0xdb2c_d7e7_b0f4_78be,
                0xabf4_641a_2c71_ba49,
                0x20c6_ed6d_9d7b_8d41,
                0x2c40_99de_223c_39d4,
            ]
        );
        assert_eq!(
            SeedSequence::from_seed((1 << 40) + 5).generate_state_u64(4),
            vec![
                0xdae4_1755_e08a_170b,
                0x8415_abe8_7ecd_cb2c,
                0x2525_c5b4_4b91_18d0,
                0x42c2_793f_3392_bed3,
            ]
        );
    }
}

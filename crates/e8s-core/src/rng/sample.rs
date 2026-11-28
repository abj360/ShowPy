//! sample --- numpy's sampling without replacement, `Generator.choice(n, size, replace=False)`
//!
//! numpy picks one of two methods by the population and the sample size, and each consumes the
//! generator in its own order, so both are kept: a tail shuffle of the whole population for a
//! dense sample of a large one, else Floyd's algorithm over an open-addressed set, followed by
//! a shuffle of the sample.
//!
//! Contains:
//!     MAX_SAMPLE: bounds the values one call samples
//!     Pcg64::choice_without_replacement(): samples distinct integers in [0, n) as numpy does

use super::pcg64::Pcg64;
use crate::base::caps::{MAX_POINTS, require_size};
use crate::base::{CoreError, CoreResult, ErrorCode, ErrorKind};

/// Bounds the values one call samples, checked before anything is allocated. The tail shuffle
/// holds a population under 50 times the sample, so it stays bounded too.
pub const MAX_SAMPLE: i64 = MAX_POINTS;

/// Populations up to this size always use Floyd's algorithm.
const TAIL_SHUFFLE_MIN_POPULATION: usize = 10_000;
/// A sample larger than one in this many of a large population uses the tail shuffle.
const TAIL_SHUFFLE_SHARE: usize = 50;
/// Floyd's set holds at least this many slots per sampled value.
const SET_LOAD: f64 = 1.2;
const EMPTY_SLOT: u64 = u64::MAX;

/// Returns the smallest all-ones bit mask at least `value`.
const fn mask_for(value: u64) -> u64 {
    let mut mask = value;
    mask |= mask >> 1;
    mask |= mask >> 2;
    mask |= mask >> 4;
    mask |= mask >> 8;
    mask |= mask >> 16;
    mask | (mask >> 32)
}

fn oversized(n: usize, size: usize) -> CoreError {
    CoreError::new(
        ErrorKind::Schema,
        ErrorCode::Schema,
        format!("cannot take a sample of {size} from a population of {n} without replacement"),
        "ask for at most as many values as the population holds",
    )
    .with_context("n", i64::try_from(n).unwrap_or(i64::MAX))
    .with_context("size", i64::try_from(size).unwrap_or(i64::MAX))
}

impl Pcg64 {
    /// Samples `size` distinct integers in [0, n), as `Generator.choice(n, size, replace=False)`
    /// with its default shuffle does.
    ///
    /// # Errors
    ///
    /// Returns a SchemaError when `size` exceeds `n`, where numpy raises ValueError, and when it
    /// exceeds MAX_SAMPLE.
    pub fn choice_without_replacement(&mut self, n: usize, size: usize) -> CoreResult<Vec<usize>> {
        require_size(
            i64::try_from(size).unwrap_or(i64::MAX),
            MAX_SAMPLE,
            "samples",
        )?;
        if size > n {
            return Err(oversized(n, size));
        }
        if n > TAIL_SHUFFLE_MIN_POPULATION && size > n / TAIL_SHUFFLE_SHARE {
            return Ok(self.tail_shuffle(n, size));
        }
        Ok(self.floyd(n, size))
    }

    /// Shuffles positions `n - 1` down to `first` of `values`, each with a draw in [0, i].
    fn shuffle_from(&mut self, values: &mut [usize], first: usize) {
        for i in (first..values.len()).rev() {
            let j = self.bounded(i as u64) as usize;
            values.swap(i, j);
        }
    }

    fn tail_shuffle(&mut self, n: usize, size: usize) -> Vec<usize> {
        let mut population: Vec<usize> = (0..n).collect();
        self.shuffle_from(&mut population, (n - size).max(1));
        population.split_off(n - size)
    }

    fn floyd(&mut self, n: usize, size: usize) -> Vec<usize> {
        let mask = mask_for((SET_LOAD * size as f64) as u64);
        let mut set = vec![EMPTY_SLOT; (mask as usize).saturating_add(1)];
        let mut sample = Vec::with_capacity(size);
        for j in (n - size)..n {
            let value = self.bounded(j as u64);
            let mut slot = value & mask;
            while set[slot as usize] != EMPTY_SLOT && set[slot as usize] != value {
                slot = (slot + 1) & mask;
            }
            if set[slot as usize] == EMPTY_SLOT {
                set[slot as usize] = value;
                sample.push(value as usize);
            } else {
                let mut free = j as u64 & mask;
                while set[free as usize] != EMPTY_SLOT {
                    free = (free + 1) & mask;
                }
                set[free as usize] = j as u64;
                sample.push(j);
            }
        }
        self.shuffle_from(&mut sample, 1);
        sample
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn masks_cover_their_value() {
        assert_eq!(mask_for(0), 0);
        assert_eq!(mask_for(1), 1);
        assert_eq!(mask_for(1200), 2047);
    }

    #[test]
    fn samples_are_distinct_and_in_range_for_both_methods() {
        for (n, size) in [(1000, 1000), (60_000, 1000), (40_000, 1000), (5, 0)] {
            let mut sample = Pcg64::from_seed(7)
                .choice_without_replacement(n, size)
                .unwrap_or_default();
            assert_eq!(sample.len(), size);
            sample.sort_unstable();
            sample.dedup();
            assert_eq!(sample.len(), size);
            assert!(sample.iter().all(|&value| value < n));
        }
    }

    #[test]
    fn an_oversized_sample_is_refused() {
        assert!(
            Pcg64::from_seed(1)
                .choice_without_replacement(3, 4)
                .is_err()
        );
    }

    #[test]
    fn a_sample_over_max_sample_is_refused_before_allocating() {
        let mut generator = Pcg64::from_seed(1);
        let over = usize::try_from(MAX_SAMPLE).unwrap_or(usize::MAX) + 1;
        assert!(generator.choice_without_replacement(over, over).is_err());
        assert!(
            generator
                .choice_without_replacement(1 << 40, 1 << 40)
                .is_err()
        );
        assert_eq!(
            generator
                .choice_without_replacement(1 << 40, 3)
                .map(|s| s.len()),
            Ok(3)
        );
    }
}

//! similar --- close-name suggestions, scored as Python's `difflib.get_close_matches` scores them
//!
//! Each candidate is scored by `difflib.SequenceMatcher(None, candidate, name).ratio()`: twice
//! the characters of its matching blocks over both lengths. The blocks come from the same
//! longest-match search (earliest in the candidate, then earliest in the name), with the same
//! automatic junk rule for names of 200 characters or more, so every score is difflib's.
//!
//! Contains:
//!     CLOSE_MATCH_COUNT: the most suggestions close_matches returns
//!     CLOSE_MATCH_CUTOFF: the lowest score a suggestion may have
//!     close_matches(): picks the best-scoring candidates for a name, best first

use std::collections::HashMap;

/// The most suggestions close_matches returns.
pub const CLOSE_MATCH_COUNT: usize = 3;
/// The lowest score a suggestion may have.
pub const CLOSE_MATCH_CUTOFF: f64 = 0.4;
/// Names this long drop their popular characters from the match index, as difflib's autojunk.
const AUTOJUNK_MIN_LENGTH: usize = 200;
const AUTOJUNK_SHARE: usize = 100;

/// The name a candidate is compared against: its characters and where each occurs.
struct Target<'a> {
    b: &'a [u32],
    /// Positions of each character in `b`, ascending; popular characters are left out.
    b2j: HashMap<u32, Vec<usize>>,
    /// How often each character occurs in `b`, popular ones included.
    counts: HashMap<u32, usize>,
}

impl<'a> Target<'a> {
    fn new(b: &'a [u32]) -> Self {
        let mut b2j: HashMap<u32, Vec<usize>> = HashMap::new();
        for (j, &c) in b.iter().enumerate() {
            b2j.entry(c).or_default().push(j);
        }
        let counts = b2j.iter().map(|(&c, places)| (c, places.len())).collect();
        if b.len() >= AUTOJUNK_MIN_LENGTH {
            let most = b.len() / AUTOJUNK_SHARE + 1;
            b2j.retain(|_, places| places.len() <= most);
        }
        Self { b, b2j, counts }
    }

    /// Counts the characters `a` and `b` share, ignoring order: an upper bound of the matches.
    fn shared(&self, a: &[u32]) -> usize {
        let mut available: HashMap<u32, isize> = HashMap::new();
        let mut matches = 0;
        for &c in a {
            let left = available.entry(c).or_insert_with(|| {
                isize::try_from(self.counts.get(&c).copied().unwrap_or(0)).unwrap_or(isize::MAX)
            });
            if *left > 0 {
                matches += 1;
            }
            *left -= 1;
        }
        matches
    }

    /// Finds the longest block of `a[alo..ahi]` equal to a block of `b[blo..bhi]`, earliest in
    /// `a` and then in `b`, extended over characters the index left out.
    fn longest_match(
        &self,
        a: &[u32],
        (alo, ahi): (usize, usize),
        (blo, bhi): (usize, usize),
    ) -> (usize, usize, usize) {
        let (mut best_i, mut best_j, mut best_size) = (alo, blo, 0);
        let mut previous: HashMap<usize, usize> = HashMap::new();
        for (i, c) in a.iter().enumerate().take(ahi).skip(alo) {
            let mut current: HashMap<usize, usize> = HashMap::new();
            for &j in self.b2j.get(c).map_or(&[][..], Vec::as_slice) {
                if j < blo {
                    continue;
                }
                if j >= bhi {
                    break;
                }
                let size = j
                    .checked_sub(1)
                    .and_then(|k| previous.get(&k))
                    .copied()
                    .unwrap_or(0)
                    + 1;
                current.insert(j, size);
                if size > best_size {
                    (best_i, best_j, best_size) = (i + 1 - size, j + 1 - size, size);
                }
            }
            previous = current;
        }
        while best_i > alo && best_j > blo && a[best_i - 1] == self.b[best_j - 1] {
            (best_i, best_j, best_size) = (best_i - 1, best_j - 1, best_size + 1);
        }
        while best_i + best_size < ahi
            && best_j + best_size < bhi
            && a[best_i + best_size] == self.b[best_j + best_size]
        {
            best_size += 1;
        }
        (best_i, best_j, best_size)
    }

    /// Counts the characters in the matching blocks of `a` and `b`.
    fn matched(&self, a: &[u32]) -> usize {
        let mut queue = vec![((0, a.len()), (0, self.b.len()))];
        let mut total = 0;
        while let Some(((alo, ahi), (blo, bhi))) = queue.pop() {
            let (i, j, size) = self.longest_match(a, (alo, ahi), (blo, bhi));
            if size == 0 {
                continue;
            }
            total += size;
            if alo < i && blo < j {
                queue.push(((alo, i), (blo, j)));
            }
            if i + size < ahi && j + size < bhi {
                queue.push(((i + size, ahi), (j + size, bhi)));
            }
        }
        total
    }
}

/// Returns difflib's ratio for `matches` characters over sequences of `length` in total.
fn ratio(matches: usize, length: usize) -> f64 {
    if length == 0 {
        return 1.0;
    }
    2.0 * matches as f64 / length as f64
}

/// Picks up to CLOSE_MATCH_COUNT candidates scoring at least CLOSE_MATCH_CUTOFF against `name`,
/// best first; equal scores put the larger candidate first, then the earlier one.
///
/// Returns the candidates' indices.
#[must_use]
pub fn close_matches(name: &[u32], candidates: &[Vec<u32>]) -> Vec<usize> {
    let target = Target::new(name);
    let mut scored: Vec<(f64, usize)> = Vec::new();
    for (index, a) in candidates.iter().enumerate() {
        let length = a.len() + name.len();
        if ratio(a.len().min(name.len()), length) < CLOSE_MATCH_CUTOFF
            || ratio(target.shared(a), length) < CLOSE_MATCH_CUTOFF
        {
            continue;
        }
        let score = ratio(target.matched(a), length);
        if score >= CLOSE_MATCH_CUTOFF {
            scored.push((score, index));
        }
    }
    scored.sort_by(|&(left_score, left), &(right_score, right)| {
        right_score
            .total_cmp(&left_score)
            .then_with(|| candidates[right].cmp(&candidates[left]))
    });
    scored
        .into_iter()
        .take(CLOSE_MATCH_COUNT)
        .map(|(_, index)| index)
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn points(text: &str) -> Vec<u32> {
        text.chars().map(u32::from).collect()
    }

    fn suggest(name: &str, candidates: &[&str]) -> Vec<String> {
        let pool: Vec<Vec<u32>> = candidates.iter().map(|text| points(text)).collect();
        close_matches(&points(name), &pool)
            .into_iter()
            .map(|index| candidates[index].to_owned())
            .collect()
    }

    #[test]
    fn finds_short_ids_best_first() {
        assert_eq!(suggest("T3", &["T1", "B7", "T12"]), vec!["T1", "T12"]);
        assert!(suggest("zzz", &["T1"]).is_empty());
    }

    #[test]
    fn matches_difflib_at_the_suggestion_cutoff() {
        assert_eq!(
            suggest("appel", &["ape", "apple", "peach", "puppy"]),
            vec!["apple", "ape", "puppy"]
        );
        assert_eq!(suggest("wheel", &["while", "while", "whale"])[0], "while");
    }

    #[test]
    fn equal_scores_put_the_larger_candidate_first() {
        assert_eq!(
            suggest("ab", &["ax", "ay", "az", "aw"]),
            vec!["az", "ay", "ax"]
        );
    }
}

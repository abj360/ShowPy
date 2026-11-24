//! cache --- one converted core value per frozen part, keyed by the part's identity
//!
//! Contains:
//!     CacheStats: counts the hits, misses and live entries of one cache
//!     PartCache: converts each frozen part once and serves the value while the part lives
//!     add_probes(): adds the private cache hooks `_cache_probe` and `_cache_stats` to a module

use std::collections::HashMap;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, LazyLock, Mutex, MutexGuard, PoisonError, Weak};

use e8s_core::base::{CoreResult, require_finite};
use pyo3::prelude::*;
use pyo3::types::{PyCFunction, PyDict, PyTuple, PyWeakrefReference};

use crate::errors::guard;

/// One cached value and the weak reference that proves which object it belongs to.
struct Entry<T> {
    part: Py<PyWeakrefReference>,
    value: Arc<T>,
}

type Entries<T> = Mutex<HashMap<usize, Entry<T>>>;

/// Counts the hits, misses and live entries of one cache.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct CacheStats {
    /// Lookups served from the cache.
    pub hits: usize,
    /// Lookups that converted their part.
    pub misses: usize,
    /// Parts that are cached now.
    pub entries: usize,
}

/// Converts each frozen part once and serves the converted value while the part lives.
///
/// Entries are keyed by `id(part)` and verified by a weak reference to the part, so an equal
/// but distinct object never gets another object's value. The weak reference's callback evicts
/// the entry when the part is collected, before CPython can reuse its address.
///
/// Locking: one `std::sync::Mutex` guards the map, held only for map reads and writes. No Python
/// code runs under it (conversion, weak reference creation and dropping evicted entries all
/// happen outside), so a collection that fires a callback mid-call cannot deadlock on it.
pub struct PartCache<T> {
    entries: Arc<Entries<T>>,
    hits: AtomicUsize,
    misses: AtomicUsize,
}

impl<T: Send + Sync + 'static> PartCache<T> {
    /// Creates an empty cache.
    #[must_use]
    pub fn new() -> Self {
        Self {
            entries: Arc::new(Mutex::new(HashMap::new())),
            hits: AtomicUsize::new(0),
            misses: AtomicUsize::new(0),
        }
    }

    /// Returns the cached value of `part`, converting and caching it on a miss.
    ///
    /// # Errors
    ///
    /// Returns the error `convert` returns, or the error of creating the weak reference when
    /// the part does not support one; nothing is cached then.
    pub fn get_or_insert_with(
        &self,
        part: &Bound<'_, PyAny>,
        convert: impl FnOnce(&Bound<'_, PyAny>) -> PyResult<T>,
    ) -> PyResult<Arc<T>> {
        let key = part.as_ptr().addr();
        if let Some(value) = self.cached(part, key) {
            self.hits.fetch_add(1, Ordering::Relaxed);
            return Ok(value);
        }
        self.misses.fetch_add(1, Ordering::Relaxed);
        let value = Arc::new(convert(part)?);
        let callback = evict_on_collect(part.py(), Arc::downgrade(&self.entries), key)?;
        let weak = PyWeakrefReference::new_with(part, callback)?.unbind();
        let replaced = lock(&self.entries).insert(
            key,
            Entry {
                part: weak,
                value: Arc::clone(&value),
            },
        );
        drop(replaced);
        Ok(value)
    }

    /// Returns the hits, misses and live entries counted so far.
    #[must_use]
    pub fn stats(&self) -> CacheStats {
        CacheStats {
            hits: self.hits.load(Ordering::Relaxed),
            misses: self.misses.load(Ordering::Relaxed),
            entries: lock(&self.entries).len(),
        }
    }

    /// Returns the value cached under `key` when its weak reference still points at `part`.
    fn cached(&self, part: &Bound<'_, PyAny>, key: usize) -> Option<Arc<T>> {
        let (weak, value) = {
            let entries = lock(&self.entries);
            let entry = entries.get(&key)?;
            (entry.part.clone_ref(part.py()), Arc::clone(&entry.value))
        };
        let referent = weak.bind(part.py()).upgrade()?;
        referent.is(part).then_some(value)
    }
}

impl<T: Send + Sync + 'static> Default for PartCache<T> {
    fn default() -> Self {
        Self::new()
    }
}

fn lock<T>(entries: &Entries<T>) -> MutexGuard<'_, HashMap<usize, Entry<T>>> {
    // No code panics while holding the lock, so a poisoned map is still consistent.
    entries.lock().unwrap_or_else(PoisonError::into_inner)
}

/// Builds the weak reference callback that evicts `key` when its part is collected.
///
/// It removes the entry only when the entry's weak reference is the one that fired, so a newer
/// entry under a reused address is never evicted by an older part's callback.
fn evict_on_collect<T: Send + Sync + 'static>(
    py: Python<'_>,
    entries: Weak<Entries<T>>,
    key: usize,
) -> PyResult<Bound<'_, PyCFunction>> {
    PyCFunction::new_closure(
        py,
        None,
        None,
        move |args: &Bound<'_, PyTuple>, _kwargs: Option<&Bound<'_, PyDict>>| -> PyResult<()> {
            let Some(entries) = entries.upgrade() else {
                return Ok(());
            };
            let fired = args.get_item(0)?;
            let evicted = {
                let mut map = lock(&entries);
                let is_fired = map.get(&key).is_some_and(|entry| entry.part.is(&fired));
                if is_fired { map.remove(&key) } else { None }
            };
            drop(evicted);
            Ok(())
        },
    )
}

/// Checks the corners of a probed `Box2`, as a core conversion checks its inputs.
fn probe_corners(corners: [f64; 4]) -> CoreResult<[f64; 4]> {
    for corner in corners {
        require_finite("corner", corner)?;
    }
    Ok(corners)
}

static PROBE_CACHE: LazyLock<PartCache<[f64; 4]>> = LazyLock::new(PartCache::new);

/// Caches a converted `Box2` and returns the address of the cached value.
///
/// Two calls return the same address exactly when the second call hit the cache.
#[pyfunction(name = "_cache_probe")]
fn cache_probe(part: &Bound<'_, PyAny>) -> PyResult<usize> {
    let value = PROBE_CACHE.get_or_insert_with(part, |part| {
        let (min_x, min_y): (f64, f64) = part.getattr("min")?.extract()?;
        let (max_x, max_y): (f64, f64) = part.getattr("max")?.extract()?;
        guard("_cache_probe", || {
            probe_corners([min_x, min_y, max_x, max_y])
        })
    })?;
    Ok(Arc::as_ptr(&value).addr())
}

/// Returns the probe cache's `hits`, `misses` and live `entries`.
#[pyfunction(name = "_cache_stats")]
fn cache_stats() -> HashMap<&'static str, usize> {
    let stats = PROBE_CACHE.stats();
    HashMap::from([
        ("hits", stats.hits),
        ("misses", stats.misses),
        ("entries", stats.entries),
    ])
}

/// Adds the private cache hooks `_cache_probe` and `_cache_stats` to a module.
///
/// # Errors
///
/// Returns the error of adding either function.
pub fn add_probes(module: &Bound<'_, PyModule>) -> PyResult<()> {
    crate::add_hook(module, wrap_pyfunction!(cache_probe, module)?.as_any())?;
    crate::add_hook(module, wrap_pyfunction!(cache_stats, module)?.as_any())
}

//! rng --- binds the seeded PCG64 generator for the tests that hold it to numpy's
//!
//! Contains:
//!     pcg64_draws(): draws integers, floats and a sample without replacement from one seed
//!     add_hooks(): adds the private hook `_pcg64_draws` to a module

use e8s_core::base::CoreResult;
use e8s_core::rng::Pcg64;
use numpy::PyArray1;
use pyo3::prelude::*;

use crate::errors::guard;

type Draws<'py> = (
    Bound<'py, PyArray1<i64>>,
    Bound<'py, PyArray1<f64>>,
    Bound<'py, PyArray1<i64>>,
);

fn draw(
    seed: u64,
    high: u64,
    n: usize,
    count: usize,
) -> CoreResult<(Vec<i64>, Vec<f64>, Vec<i64>)> {
    let mut generator = Pcg64::from_seed(seed);
    let integers = (0..count)
        .map(|_| generator.integers(high).map(|value| value as i64))
        .collect::<CoreResult<Vec<_>>>()?;
    let floats = (0..count).map(|_| generator.random()).collect();
    let sample = generator
        .choice_without_replacement(n, count)?
        .into_iter()
        .map(|value| value as i64)
        .collect();
    Ok((integers, floats, sample))
}

/// Seeds one generator and draws, in order, `count` integers in [0, high), `count` floats in
/// [0, 1) and a sample of `count` distinct integers in [0, n), as numpy's
/// `Generator(PCG64(seed))` does with `integers(high, size=count)`, `random(count)` and
/// `choice(n, size=count, replace=False)`.
#[pyfunction(name = "_pcg64_draws")]
fn pcg64_draws(
    py: Python<'_>,
    seed: u64,
    high: u64,
    n: usize,
    count: usize,
) -> PyResult<Draws<'_>> {
    let (integers, floats, sample) = guard("_pcg64_draws", || draw(seed, high, n, count))?;
    Ok((
        PyArray1::from_vec(py, integers),
        PyArray1::from_vec(py, floats),
        PyArray1::from_vec(py, sample),
    ))
}

/// Adds the private hook `_pcg64_draws` to a module.
///
/// # Errors
///
/// Returns the error of adding the function.
pub fn add_hooks(module: &Bound<'_, PyModule>) -> PyResult<()> {
    crate::add_hook(module, wrap_pyfunction!(pcg64_draws, module)?.as_any())
}

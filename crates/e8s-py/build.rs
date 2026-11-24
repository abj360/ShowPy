//! build --- lets the binding's test binaries find the libpython they embed
//!
//! Contains:
//!     main(): adds an rpath to the Python library folder to the test binaries' link

/// Adds an rpath to the Python library folder to the test binaries' link, on Unix hosts.
///
/// `cargo test` links libpython into the test binaries. Linux finds it by its soname only
/// through a search path, so the tests carry the folder pyo3 linked against. Wheels are built
/// without the tests, so they never carry it.
fn main() {
    let is_unix = std::env::var("CARGO_CFG_TARGET_FAMILY").is_ok_and(|family| family == "unix");
    if let Some(lib_dir) = pyo3_build_config::get().lib_dir()
        && is_unix
    {
        println!("cargo:rustc-link-arg-tests=-Wl,-rpath,{lib_dir}");
    }
}

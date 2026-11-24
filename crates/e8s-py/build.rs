//! build --- lets the binding's test binaries find the libpython they embed
//!
//! Contains:
//!     main(): adds an rpath to the Python library folder to the test binaries' link

/// Adds an rpath to the Python library folder to the test binaries' link, on Unix hosts.
///
/// `cargo test` links libpython into the test binaries. Linux finds it by its soname only
/// through a search path, so the tests carry the folder pyo3 linked against. Wheels are built
/// without the tests, so they never carry it. Each `-Xlinker` hands the linker one argument
/// as is, so a folder whose name holds a comma stays whole, where `-Wl,` would split it. The
/// script reruns when PYO3_PYTHON picks another Python, and so another folder.
fn main() {
    println!("cargo:rerun-if-env-changed=PYO3_PYTHON");
    let is_unix = std::env::var("CARGO_CFG_TARGET_FAMILY").is_ok_and(|family| family == "unix");
    if let Some(lib_dir) = pyo3_build_config::get().lib_dir()
        && is_unix
    {
        for argument in ["-Xlinker", "-rpath", "-Xlinker", lib_dir] {
            println!("cargo:rustc-link-arg-tests={argument}");
        }
    }
}

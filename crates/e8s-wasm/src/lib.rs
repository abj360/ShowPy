//! e8s-wasm --- WebAssembly bindings of the e8s core, built for `wasm32-unknown-unknown`

#![forbid(unsafe_code)]

// Linked before the first export calls it, so the wasm build compiles the core.
use e8s_core as _;

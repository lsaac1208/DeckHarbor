#!/bin/bash
# Build from the project root using the host's normal Rust/C linker toolchain.
set -euo pipefail
cd "$(dirname "$0")"
command -v cargo >/dev/null || { echo "Install Rust and a C compiler first." >&2; exit 1; }
cargo build --manifest-path newfreedeck_core/Cargo.toml --release --locked
cp newfreedeck_core/target/release/libnewfreedeck_core.so py_modules/
echo "Built py_modules/libnewfreedeck_core.so"

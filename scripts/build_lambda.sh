#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
rm -rf "$ROOT/build"
mkdir -p "$ROOT/build/package"

# Install all runtime dependencies into the package dir
pip install --quiet --target "$ROOT/build/package" "$ROOT"

# Copy source packages on top so handlers are importable at the top level
cp -R "$ROOT/src/market_ai" "$ROOT/build/package/"
cp -R "$ROOT/src/app" "$ROOT/build/package/"

cd "$ROOT/build/package"
zip -qr ../lambda.zip .
echo "Created build/lambda.zip ($(du -sh ../lambda.zip | cut -f1))"

#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
rm -rf "$ROOT/build"
mkdir -p "$ROOT/build/package"
# Copy both the legacy handlers and the new app layer
cp -R "$ROOT/src/market_ai" "$ROOT/build/package/"
cp -R "$ROOT/src/app" "$ROOT/build/package/"
cd "$ROOT/build/package"
zip -qr ../lambda.zip .
echo "Created build/lambda.zip"

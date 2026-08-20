#!/usr/bin/env bash
# Builds a self-contained organization-distribution zip:
#   - Plugin manifests (.claude-plugin/plugin.json + marketplace.json)
#   - Hook config and the Python hook (hooks/)
#   - README.md
#
# The hooks are plain Python running on the machine's own python3, so the zip
# is platform-independent and carries no binaries or install step.
#
# Output: dist/clover-plugin-v<version>.zip
#
# Usage:
#   ./scripts/build-org-zip.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

VERSION=$(grep -o '"version"[[:space:]]*:[[:space:]]*"[^"]*"' .claude-plugin/plugin.json | grep -o '[0-9][0-9.]*')
echo "Building offline distribution for clover-plugin v${VERSION}"

STAGE="dist/clover-plugin"
rm -rf dist
mkdir -p "$STAGE/.claude-plugin"

cp .claude-plugin/plugin.json .claude-plugin/marketplace.json "$STAGE/.claude-plugin/"
cp -R hooks "$STAGE/"
cp README.md "$STAGE/"

# Bundle skills when present (auto-discovered from skills/<name>/SKILL.md).
if [ -d skills ]; then
    cp -R skills "$STAGE/"
fi

ZIP="dist/clover-plugin-v${VERSION}.zip"
( cd dist && zip -r "$(basename "$ZIP")" clover-plugin >/dev/null )

SIZE=$(du -h "$ZIP" | cut -f1)
echo
echo "Done: $ZIP ($SIZE)"
echo
echo "To install in your Claude Code organization:"
echo "  unzip $ZIP -d ~/clover-plugin && \\"
echo "  claude plugin install ~/clover-plugin/clover-plugin"
echo
echo "Or distribute the zip directly — Claude Code can install from a local path."

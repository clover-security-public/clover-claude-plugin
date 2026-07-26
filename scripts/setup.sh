#!/bin/bash
# Deploys the clover-hook binary bundled with this plugin clone.
# Uses ${CLAUDE_PLUGIN_DATA} for persistent storage across plugin updates.
#
# This script makes no network calls. The binary it deploys always comes from
# this clone's bin/ directory, so the running artifact matches the reviewed,
# pinned version of the plugin.

# Persist plugin options to env.sh so other hook events
# (PreToolUse) — which do not receive CLAUDE_PLUGIN_OPTION_* env vars —
# can read them via run-hook.sh.
# printf %q quotes values safely for re-sourcing.
#
# Only rewrite env.sh when at least one CLAUDE_PLUGIN_OPTION_* is non-empty.
# This guard is load-bearing for two reasons:
#   1. setup.sh is invoked from run-hook.sh (self-bootstrap path) where
#      CLAUDE_PLUGIN_OPTION_* are not in scope. Without the guard we'd
#      truncate env.sh on every hook fire, clobbering values the user
#      provided via settings.json's `env` block.
#   2. Empty exports inside env.sh override real values inherited from
#      Claude Code's process env, breaking auth.
HAS_OPTIONS="${CLAUDE_PLUGIN_OPTION_CLIENT_ID:-}${CLAUDE_PLUGIN_OPTION_CLIENT_SECRET:-}${CLAUDE_PLUGIN_OPTION_AUTH_URL:-}${CLAUDE_PLUGIN_OPTION_SERVER_URL:-}"
if [ -n "${CLAUDE_PLUGIN_DATA}" ] && [ -n "$HAS_OPTIONS" ]; then
  mkdir -p "${CLAUDE_PLUGIN_DATA}"
  ENV_FILE="${CLAUDE_PLUGIN_DATA}/env.sh"
  {
    [ -n "${CLAUDE_PLUGIN_OPTION_CLIENT_ID:-}" ]     && printf 'export CAS_CLOVER_PLUGIN_CLIENT_ID=%q\n'     "${CLAUDE_PLUGIN_OPTION_CLIENT_ID}"
    [ -n "${CLAUDE_PLUGIN_OPTION_CLIENT_SECRET:-}" ] && printf 'export CAS_CLOVER_PLUGIN_CLIENT_SECRET=%q\n' "${CLAUDE_PLUGIN_OPTION_CLIENT_SECRET}"
    [ -n "${CLAUDE_PLUGIN_OPTION_AUTH_URL:-}" ]      && printf 'export CAS_CLOVER_PLUGIN_AUTH_URL=%q\n'      "${CLAUDE_PLUGIN_OPTION_AUTH_URL}"
    [ -n "${CLAUDE_PLUGIN_OPTION_SERVER_URL:-}" ]    && printf 'export CAS_CLOVER_PLUGIN_SERVER_URL=%q\n'    "${CLAUDE_PLUGIN_OPTION_SERVER_URL}"
    true
  } > "$ENV_FILE"
  chmod 600 "$ENV_FILE"
fi
# Make sure CLAUDE_PLUGIN_DATA exists even when no options are supplied,
# so the binary install below has a stable target dir.
[ -n "${CLAUDE_PLUGIN_DATA}" ] && mkdir -p "${CLAUDE_PLUGIN_DATA}"

# ===========================================================================
# Binary management — deploy the clover-hook binary bundled with this clone.
#
# Contract:
#   1. Offline only. Nothing here reaches the network. The binary is copied
#      from ${CLAUDE_PLUGIN_ROOT}/bin, which ships inside the plugin package,
#      and that is its only possible source. A missing bundled asset is a
#      hard error, never a remote fallback.
#   2. The deployed binary always matches the version in this clone's
#      plugin.json. New versions arrive by updating the plugin through the
#      marketplace, never at runtime.
#   3. The version check is an exact match, in both directions. A deployed
#      binary that is *ahead* of the clone — left behind by the runtime
#      self-update this script used to perform — is replaced too, so every
#      machine converges on the version the clone pins.
#
# Accepts and ignores any arguments; callers used to pass --no-update to
# suppress the network path, which no longer exists.
# ===========================================================================
BINARY_DIR="${CLAUDE_PLUGIN_DATA:-${CLAUDE_PLUGIN_ROOT}}/bin"
BINARY="$BINARY_DIR/clover-hook"
VERSION_FILE="$BINARY_DIR/.version"

# Detect platform
OS=$(uname -s | tr '[:upper:]' '[:lower:]')
ARCH=$(uname -m)
case "$ARCH" in
  x86_64) ARCH="amd64" ;;
  aarch64|arm64) ARCH="arm64" ;;
esac
ASSET_NAME="clover-hook-${OS}-${ARCH}"

# Version shipped in this clone — the one and only version we deploy.
CLONE_VERSION=$(grep -o '"version"[[:space:]]*:[[:space:]]*"[^"]*"' "${CLAUDE_PLUGIN_ROOT}/.claude-plugin/plugin.json" 2>/dev/null | grep -o '[0-9][0-9.]*')
DEPLOYED_VERSION=$(cat "$VERSION_FILE" 2>/dev/null || echo "")

# Already deployed at exactly the clone's version → nothing to do.
if [ -x "$BINARY" ] && [ -n "$CLONE_VERSION" ] && [ "$DEPLOYED_VERSION" = "$CLONE_VERSION" ]; then
  exit 0
fi

mkdir -p "$BINARY_DIR"

BUNDLED="${CLAUDE_PLUGIN_ROOT}/bin/${ASSET_NAME}"
if [ ! -f "$BUNDLED" ]; then
  echo "clover-plugin setup.sh: bundled binary not found for ${OS}/${ARCH} (expected ${BUNDLED})" >&2
  exit 1
fi

cp "$BUNDLED" "$BINARY"
chmod +x "$BINARY"
echo "$CLONE_VERSION" > "$VERSION_FILE"

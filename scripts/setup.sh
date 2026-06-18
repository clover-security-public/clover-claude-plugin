#!/bin/bash
# Downloads the correct clover-hook binary from GitHub releases on first run.
# Uses ${CLAUDE_PLUGIN_DATA} for persistent storage across plugin updates.

REPO="clover-security/clover-claude-plugin"

# Persist plugin options to env.sh so other hook events
# (UserPromptSubmit, PreToolUse) — which do not receive
# CLAUDE_PLUGIN_OPTION_* env vars — can read them via run-hook.sh.
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

# ---------------------------------------------------------------------------
# Registry self-heal — workaround for split-brain plugin state.
#
# TODO(clover-coding-plugin): investigate the root cause and remove this
# block once Claude Code reliably writes installed_plugins.json for managed
# plugin installs.
#
# Some users hit a state where:
#   - The plugin cache is fully populated (~/.claude/plugins/cache/...).
#   - The marketplace is registered (~/.claude/plugins/known_marketplaces.json).
#   - But installed_plugins.json has NO entry for clover@clover-security.
# Claude Code then prints "Plugin clover not cached at (not recorded)" on
# every new session until the user runs /reload-plugins (which patches the
# in-memory state for that session but never persists back to the registry).
#
# Diagnosis (read-only Claude Code agent run on an affected machine,
# 2026-05-13) confirmed this is the case for at least one user (Ron):
#   - cache dir contained .orphaned_at + .in_use markers simultaneously
#   - hook log showed the binary successfully auth+POSTing
#   - claude plugin list omitted clover entirely
#
# Suspected triggers (NOT confirmed):
#   - A previous uninstall that didn't propagate to cache/data dirs.
#   - Interrupted update / scope migration.
#   - A managed-settings deployment race where the install record was
#     lost between manifest reads.
#
# This block is fully idempotent and safe to run on healthy machines:
#   - If installed_plugins.json already has a valid entry, it's a no-op.
#   - If it doesn't, it writes a 'user'-scope entry pointing at the
#     current CLAUDE_PLUGIN_ROOT (which IS the cache dir Claude Code
#     itself would have referenced if the registry write had succeeded).
#   - Atomic write (temp file + rename) so we never leave the file in
#     a torn state if interrupted.
#   - Wrapped in try/except — any failure logs to stderr and exits 0,
#     never blocks setup.sh from completing the rest of its work.
#
# Removal criteria: when we have evidence (Datadog + Claude Code release
# notes) that managed plugins reliably write installed_plugins.json on
# install, this whole block can be deleted. The investigation issue should
# track that evidence.
if [ -n "${CLAUDE_PLUGIN_ROOT}" ] && command -v python3 >/dev/null 2>&1; then
  python3 - "$CLAUDE_PLUGIN_ROOT" <<'PYEOF' 2>&1 || true
import json
import os
import pathlib
import sys
import tempfile
import datetime

try:
    plugin_root = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("CLAUDE_PLUGIN_ROOT", "")
    if not plugin_root or not pathlib.Path(plugin_root).is_dir():
        sys.exit(0)

    registry_path = pathlib.Path.home() / ".claude" / "plugins" / "installed_plugins.json"
    if not registry_path.parent.exists():
        # No plugins dir at all — first-time user. Let Claude Code do its
        # normal thing; don't pre-create state we don't own.
        sys.exit(0)

    if registry_path.exists():
        try:
            data = json.loads(registry_path.read_text())
        except Exception:
            # Corrupt registry — don't risk a worse rewrite. Bail loudly.
            sys.stderr.write("clover setup.sh: installed_plugins.json is unreadable, skipping registry self-heal\n")
            sys.exit(0)
    else:
        data = {"version": 2, "plugins": {}}

    plugins = data.setdefault("plugins", {})
    existing = plugins.get("clover@clover-security", [])

    # Happy-path check: do we already have a valid entry whose installPath
    # actually points at an existing directory? If yes, no-op.
    if existing:
        valid = any(
            isinstance(e, dict)
            and e.get("installPath")
            and pathlib.Path(e["installPath"]).is_dir()
            for e in existing
        )
        if valid:
            sys.exit(0)

    # Read version from the plugin's own plugin.json so the entry matches
    # what Claude Code would have written itself.
    version = "unknown"
    plugin_json = pathlib.Path(plugin_root) / ".claude-plugin" / "plugin.json"
    if plugin_json.exists():
        try:
            version = json.loads(plugin_json.read_text()).get("version", "unknown") or "unknown"
        except Exception:
            pass

    # Best-effort: read HEAD SHA from the marketplace clone so the entry
    # shape matches what Claude Code's own writer produces for installs
    # (working plugins like csharp-lsp/superpowers have gitCommitSha set).
    # Field is genuinely optional — Claude Code accepts entries without it
    # (e.g., playground@claude-plugins-official has none).
    git_commit_sha = None
    head_file = (
        pathlib.Path.home()
        / ".claude" / "plugins" / "marketplaces"
        / "clover-security" / ".git" / "HEAD"
    )
    if head_file.exists():
        try:
            head = head_file.read_text().strip()
            if head.startswith("ref: "):
                ref_path = head_file.parent / head[5:]
                if ref_path.exists():
                    git_commit_sha = ref_path.read_text().strip()
            else:
                git_commit_sha = head
        except Exception:
            pass

    now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None).isoformat() + "Z"
    entry = {
        "scope": "user",
        "installPath": plugin_root,
        "version": version,
        "installedAt": now,
        "lastUpdated": now,
    }
    if git_commit_sha:
        entry["gitCommitSha"] = git_commit_sha
    plugins["clover@clover-security"] = [entry]

    # Atomic write: temp file + rename, so we never leave registry.json
    # half-written if the process is killed mid-write.
    tmp_fd, tmp_path = tempfile.mkstemp(
        prefix=".installed_plugins.", suffix=".tmp", dir=str(registry_path.parent)
    )
    try:
        with os.fdopen(tmp_fd, "w") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
        os.replace(tmp_path, registry_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        raise

    sys.stderr.write(
        f"clover setup.sh: registry self-heal — wrote installed_plugins entry for {version} at {plugin_root}\n"
    )
except Exception as exc:
    # Never let registry-heal failures block setup.sh.
    sys.stderr.write(f"clover setup.sh: registry self-heal skipped due to error: {exc}\n")
    sys.exit(0)
PYEOF
fi
# ---------------------------------------------------------------------------

# ===========================================================================
# Binary management — deploy and auto-update the clover-hook binary.
#
# The binary carries Clover's plan-review logic, so keeping it current is the
# whole point of auto-update. The plugin clone (CLAUDE_PLUGIN_ROOT) only
# refreshes when the user updates the plugin — which may never happen — so we
# additionally poll GitHub Releases for a newer binary and pull it into
# CLAUDE_PLUGIN_DATA, which persists across plugin updates.
#
# Design constraints, in priority order:
#   1. Offline-first — a missing network must never break the plugin. The
#      bundled binary that ships in the clone is always the fallback.
#   2. No per-hook network — the update check is TTL-gated, and the
#      latency-sensitive hook path (run-hook.sh) calls us with --no-update,
#      so only SessionStart ever reaches out to the network.
#   3. Never downgrade — we deploy max(clone version, latest released).
#
# Releases live in the per-plugin tag namespace "clover-v<version>"
# (see .github/workflows/release.yml). The legacy "v<version>" tags are
# tried as a fallback so older releases still resolve.
# ===========================================================================
UPDATE_TTL_SECONDS=21600   # 6h — how often SessionStart re-checks for a release

# --no-update: ensure a working binary is present without any network calls.
# Used by run-hook.sh on the hot hook path. SessionStart omits it.
CHECK_UPDATES=true
[ "${1:-}" = "--no-update" ] && CHECK_UPDATES=false

BINARY_DIR="${CLAUDE_PLUGIN_DATA:-${CLAUDE_PLUGIN_ROOT}}/bin"
BINARY="$BINARY_DIR/clover-hook"
VERSION_FILE="$BINARY_DIR/.version"
CHECK_FILE="$BINARY_DIR/.last_update_check"
mkdir -p "$BINARY_DIR"

# Detect platform
OS=$(uname -s | tr '[:upper:]' '[:lower:]')
ARCH=$(uname -m)
case "$ARCH" in
  x86_64) ARCH="amd64" ;;
  aarch64|arm64) ARCH="arm64" ;;
esac
ASSET_NAME="clover-hook-${OS}-${ARCH}"

# Version shipped in the current clone — the floor we never go below.
CLONE_VERSION=$(grep -o '"version"[[:space:]]*:[[:space:]]*"[^"]*"' "${CLAUDE_PLUGIN_ROOT}/.claude-plugin/plugin.json" 2>/dev/null | grep -o '[0-9][0-9.]*')
DEPLOYED_VERSION=$(cat "$VERSION_FILE" 2>/dev/null || echo "")

# Echo the higher of two dotted-numeric versions ("" counts as lowest).
# Pure bash so it works on macOS's bash 3.2 (no `sort -V` dependency).
higher_version() {
  [ -z "$1" ] && { echo "$2"; return; }
  [ -z "$2" ] && { echo "$1"; return; }
  [ "$1" = "$2" ] && { echo "$1"; return; }
  local i n; local -a a b
  IFS=. read -ra a <<< "$1"
  IFS=. read -ra b <<< "$2"
  n=${#a[@]}; [ ${#b[@]} -gt "$n" ] && n=${#b[@]}
  for (( i = 0; i < n; i++ )); do
    local x=${a[i]:-0} y=${b[i]:-0}
    if [ "$x" -gt "$y" ] 2>/dev/null; then echo "$1"; return; fi
    if [ "$x" -lt "$y" ] 2>/dev/null; then echo "$2"; return; fi
  done
  echo "$1"
}

# Newest released clover version (highest clover-v* tag). Echoes nothing on
# failure (offline / no gh / no curl) so callers fall back to the clone.
latest_released_version() {
  local tags result="" v
  if command -v gh >/dev/null 2>&1; then
    tags=$(gh release list --repo "$REPO" --limit 50 --json tagName --jq '.[].tagName' 2>/dev/null \
           | sed -n 's/^clover-v\([0-9.][0-9.]*\)$/\1/p')
  else
    tags=$(curl -fsSL "https://api.github.com/repos/$REPO/releases?per_page=50" 2>/dev/null \
           | grep -o '"tag_name"[[:space:]]*:[[:space:]]*"clover-v[0-9.]*"' \
           | sed -E 's/.*clover-v([0-9.]+).*/\1/')
  fi
  for v in $tags; do result=$(higher_version "$result" "$v"); done
  echo "$result"
}

# Download the binary for $1 into $BINARY. Tries the per-plugin tag first,
# then the legacy tag, via gh then curl. Returns 0 on success.
download_binary() {
  local version="$1" tag
  for tag in "clover-v${version}" "v${version}"; do
    if command -v gh >/dev/null 2>&1; then
      if gh release download "$tag" --repo "$REPO" --pattern "$ASSET_NAME" \
           --dir "$BINARY_DIR" --clobber >/dev/null 2>&1 \
         && [ -f "$BINARY_DIR/$ASSET_NAME" ]; then
        mv "$BINARY_DIR/$ASSET_NAME" "$BINARY"; chmod +x "$BINARY"; return 0
      fi
    fi
    if curl -fsSL "https://github.com/$REPO/releases/download/${tag}/${ASSET_NAME}" \
         -o "$BINARY.tmp" 2>/dev/null && [ -s "$BINARY.tmp" ]; then
      mv "$BINARY.tmp" "$BINARY"; chmod +x "$BINARY"; return 0
    fi
    rm -f "$BINARY.tmp"
  done
  return 1
}

# Copy the bundled binary that ships with the clone. Works offline and
# without gh auth — the always-available fallback. Returns 0 on success.
deploy_bundled() {
  local bundled="${CLAUDE_PLUGIN_ROOT}/bin/${ASSET_NAME}"
  if [ -f "$bundled" ]; then
    cp "$bundled" "$BINARY"; chmod +x "$BINARY"; return 0
  fi
  return 1
}

# ---- Decide the target version --------------------------------------------
TARGET_VERSION="$CLONE_VERSION"

if [ "$CHECK_UPDATES" = true ]; then
  # TTL gate: skip the network when the binary is present, not behind the
  # clone, and we checked recently. Anything stale forces a check.
  WITHIN_TTL=false
  if [ -f "$CHECK_FILE" ]; then
    LAST=$(cat "$CHECK_FILE" 2>/dev/null || echo 0)
    [ $(( $(date +%s) - LAST )) -lt "$UPDATE_TTL_SECONDS" ] && WITHIN_TTL=true
  fi

  if [ -x "$BINARY" ] && [ "$WITHIN_TTL" = true ] \
     && [ "$(higher_version "$DEPLOYED_VERSION" "$CLONE_VERSION")" = "$DEPLOYED_VERSION" ]; then
    exit 0
  fi

  TARGET_VERSION=$(higher_version "$CLONE_VERSION" "$(latest_released_version)")
  date +%s > "$CHECK_FILE"   # record the attempt so we don't re-poll until TTL
fi

# Already at or above the target → nothing to do (and never downgrade).
if [ -x "$BINARY" ] && [ -n "$DEPLOYED_VERSION" ] \
   && [ "$(higher_version "$DEPLOYED_VERSION" "$TARGET_VERSION")" = "$DEPLOYED_VERSION" ]; then
  exit 0
fi

# ---- Deploy the target version --------------------------------------------
# Prefer the bundled binary when the target matches the clone (fast, offline,
# no auth). Otherwise download the newer release; fall back to bundled.
if [ "$TARGET_VERSION" = "$CLONE_VERSION" ] && deploy_bundled; then
  echo "$TARGET_VERSION" > "$VERSION_FILE"
  exit 0
fi

if download_binary "$TARGET_VERSION"; then
  echo "$TARGET_VERSION" > "$VERSION_FILE"
  echo "clover-plugin setup.sh: updated binary to ${TARGET_VERSION}" >&2
  exit 0
fi

# Last resort: whatever ships in the clone keeps the plugin working.
if deploy_bundled; then
  echo "$CLONE_VERSION" > "$VERSION_FILE"
  exit 0
fi

echo "clover-plugin setup.sh: failed to install binary for ${OS}/${ARCH} (looked at bundled, gh release, curl)" >&2
exit 1

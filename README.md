# Clover — Claude Code Security Plugin

Automatically reviews implementation plans for security requirements before code is written.

## How it works

When you exit plan mode in Claude Code, Clover intercepts the plan, sends it for security analysis, and injects any missing security requirements back into the plan before implementation begins.

## Install

**1. Add the Clover marketplace:**

```bash
claude plugin marketplace add https://github.com/clover-security-public/clover-claude-plugin.git
```

**2. Install the plugin:**

```bash
claude plugin install clover
```

You'll be prompted for:
- **server_url** — your Clover API server URL (e.g. `https://app.cloversec.io`)
- **auth_url** — your Frontegg auth URL (e.g. `https://clover.frontegg.com`)
- **client_id** — API client ID (from Clover Settings > API Tokens)
- **client_secret** — API client secret

## Staying up to date

Everything Clover runs — the security engine (hook binary) and its hooks —
ships inside the plugin, so it all updates together when the marketplace is
pulled. Clover never downloads or updates anything at runtime:
the binary you run is the one bundled in the version you installed.

Enable marketplace auto-update once and Claude Code refreshes it on every
session start:

```bash
claude plugin marketplace add \
  --auto-update https://github.com/clover-security-public/clover-claude-plugin.git
```

Or toggle it any time via `/plugin` → **Marketplaces** → *clover-security* →
**Auto-update**. (You can also set it directly in `~/.claude/settings.json`
under `extraKnownMarketplaces[].autoUpdate: true`.) After an update Claude
Code may prompt you to run `/reload-plugins` to apply it in the current
session.

> Auto-update for third-party marketplaces is **off by default** — Claude Code
> can't be forced into it from our side, so enabling it is a one-time step on
> your machine. Without it, run `claude plugin marketplace update clover-security`
> when you want the latest version.

## What happens

1. You create a plan in Claude Code
2. When you exit plan mode → Clover reviews the plan
3. If security requirements are missing → Claude updates the plan
4. You approve the final plan → implementation begins

## Configuration

Override via environment variables:
```bash
export CAS_CLOVER_PLUGIN_SERVER_URL=https://app.cloversec.io
export CAS_CLOVER_PLUGIN_AUTH_URL=https://clover.frontegg.com
export CAS_CLOVER_PLUGIN_CLIENT_ID=your-client-id
export CAS_CLOVER_PLUGIN_CLIENT_SECRET=your-client-secret
```

## Logs

Debug logs at `/tmp/clover-hook.log`

## Privacy
This project is subject to the privacy practices described in our Privacy Policy:
🔒 [Privacy Policy](https://clover.security/privacy-policy)


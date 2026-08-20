# Clover — Claude Code Security Plugin

Automatically reviews implementation plans for security requirements before code is written.

## How it works

Clover intercepts a plan at two points — when you exit plan mode, and when the
agent writes a plan to a `.md` file — sends it for security analysis, and
injects any missing security requirements back into the plan before
implementation begins.

Both hooks are plain Python (`hooks/clover_hook.py`, standard library only) and
run on the `python3` already on your machine. Nothing is compiled, downloaded,
or installed. **Requires Python 3.9+.**

Every hook fails open, and does so *silently*: if Clover can't reach the
server, isn't configured, or hits an error, it exits without a decision and the
tool call follows whatever permission flow you already configured. Clover only
ever emits one decision — a deny, with the missing security requirements as its
reason. It never approves a tool call on your behalf.

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
- **user_email** — *optional*; only needed if Claude Code has no account
  profile (e.g. API-key auth). Otherwise Clover uses your Claude account email,
  falling back to your git identity.

## Staying up to date

Everything Clover runs ships inside the plugin as source, so it all updates
together when the marketplace is pulled. Clover never downloads or updates
anything at runtime: the code you run is the code bundled in the version you
installed.

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
2. When you exit plan mode — or the agent writes the plan to a `.md` file —
   Clover reviews it
3. If security requirements are missing → Claude updates the plan
4. You approve the final plan → implementation begins

A plan approved by one of the two gates is remembered for the session, so the
other gate never re-reviews the same content.

## Configuration

Override via environment variables:
```bash
export CAS_CLOVER_PLUGIN_SERVER_URL=https://app.cloversec.io
export CAS_CLOVER_PLUGIN_AUTH_URL=https://clover.frontegg.com
export CAS_CLOVER_PLUGIN_CLIENT_ID=your-client-id
export CAS_CLOVER_PLUGIN_CLIENT_SECRET=your-client-secret
export CAS_CLOVER_PLUGIN_USER_EMAIL=you@example.com   # optional
```

## Logs

Debug logs at `/tmp/.clover-hook.log` (capped at 5 MB, rotated once).

If the log says `tls no usable trust store`, your `python3` has no CA
certificates — most often a python.org build whose *Install Certificates*
step was never run. Point Clover at a bundle explicitly:

```bash
export SSL_CERT_FILE=/etc/ssl/cert.pem
```

## Privacy
This project is subject to the privacy practices described in our Privacy Policy:
🔒 [Privacy Policy](https://clover.security/privacy-policy)


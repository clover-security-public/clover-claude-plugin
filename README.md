# Clover — Claude Code Security Plugin

Automatically reviews implementation plans for security requirements before code is written.

## How it works

When you exit plan mode in Claude Code, Clover intercepts the plan, sends it for security analysis, and injects any missing security requirements back into the plan before implementation begins.

## Install

**1. Add the Clover marketplace:**

```bash
claude plugin marketplace add https://github.com/clover-security/clover-claude-plugin.git
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

## Clover for AppSec (`clover-appsec`)

A separate, lightweight plugin in the same marketplace that exposes Clover's `ask_clover_agent` tool — ask Clover about an application's security posture, run security reviews, surface threats and required controls, and design securely, all answered in natural language from inside your coding agent. No binary, no client credentials — auth is the standard OAuth browser flow.

**1. Add the Clover marketplace** (same as above, skip if already added):

```bash
claude plugin marketplace add https://github.com/clover-security/clover-claude-plugin.git
```

**2. Install the AppSec plugin:**

```bash
claude plugin install clover-appsec
```

No configuration is required — the plugin defaults to the Clover streaming host `https://streaming.cloversec.io` and connects to `${CAS_CLOVER_PLUGIN_STREAMING_URL:-https://streaming.cloversec.io}/streaming/mcp`. For self-hosted/on-prem deployments, override the host via an environment variable:

```bash
export CAS_CLOVER_PLUGIN_STREAMING_URL=https://streaming.your-domain.com
```

> Note: this is a **different host** from the `clover` developer plugin's `CAS_CLOVER_PLUGIN_SERVER_URL` (`https://app.cloversec.io`). `ask_clover_agent` runs a long streaming turn and is served only on the streaming host's `/streaming/mcp` endpoint — hence its own variable.

**3. Log in:** on first connect, Claude Code opens an OAuth browser flow. Run `/mcp` if it doesn't start automatically. Once complete, the `clover-appsec` server shows as connected and the `ask_clover_agent` tool is available.

The two plugins are independent — install `clover`, `clover-appsec`, or both.

## What happens

1. You create a plan in Claude Code
2. When you exit plan mode → Clover reviews the plan
3. If security requirements are missing → Claude updates the plan
4. You approve the final plan → implementation begins

## Configuration

Override via environment variables:
```bash
# clover (developer plugin)
export CAS_CLOVER_PLUGIN_SERVER_URL=https://app.cloversec.io
export CAS_CLOVER_PLUGIN_AUTH_URL=https://clover.frontegg.com
export CAS_CLOVER_PLUGIN_CLIENT_ID=your-client-id
export CAS_CLOVER_PLUGIN_CLIENT_SECRET=your-client-secret

# clover-appsec (streaming MCP host — defaults to https://streaming.cloversec.io)
export CAS_CLOVER_PLUGIN_STREAMING_URL=https://streaming.cloversec.io
```

## Skills

- **`/security-requirements <mode>`** — Claude silently threat-models the work in flight, prints a short `## Threats considered` block, and folds mitigations into the plan. **No questions asked.** Modes:
  - `threat-questions` — STRIDE pass over the current plan/request when it touches auth, user input, sensitive data, network, or third-party APIs.

  Also fires proactively when a plan touches a sensitive area.

## Logs

Debug logs at `/tmp/clover-hook.log`

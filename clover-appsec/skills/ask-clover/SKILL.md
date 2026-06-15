---
name: ask-clover
description: Ask Clover — a professional AppSec assistant — to review designs, model threats, discuss mitigations, and manage Clover's security resources (reviews, applications, threat models). Clover answers in natural language and may ask a follow-up or a yes/no confirmation before writing anything. Trigger when the user wants a security/design review, asks about threats, required controls, or security requirements for an app or feature, wants help designing something securely, wants to read or update Clover reviews/applications/threat models, or explicitly says "ask Clover". Calls the `ask_clover_agent` MCP tool from the clover-appsec plugin.
---

# Ask Clover (ask_clover_agent)

`ask_clover_agent` is your interface to **Clover**. It is two things at once:

1. **A management layer over Clover's resources** — it reads *and writes* security reviews,
   applications, threat models, and the like. Use it to look those up or to change them.
2. **A professional AppSec assistant** — Clover can review a design, perform threat modeling,
   discuss mitigations and required controls, and advise on secure design.

When a security question or task should be grounded in **Clover's data** or answered by **Clover's
expertise** — rather than your own local reasoning — call this tool.

## When to use it

- A **security or design review** of an app, service, feature, or change.
- **Threat modeling**, attack surface, abuse cases.
- **Mitigations, required controls, or security requirements** for a feature or design.
- Reading or **updating Clover resources** — reviews, applications, threat models.

For a quick local STRIDE pass folded into a plan with no round-trip to Clover, use the separate
`/security-requirements` skill instead.

## How Clover responds

- Answers in **natural language** — relay its `response` to the user; don't bury it.
- May ask a **follow-up question** for missing context — surface it, get the answer, call again.
- **Before writing anything**, Clover asks a **yes/no confirmation**. Present it to the user and only
  proceed once they answer. Your answer is just another call in the same conversation.

## The `chat_id` continuity contract (most important)

Clover threads a conversation by `chat_id`. Get this wrong and every call is a fresh conversation
with no memory of the last.

1. **First call:** omit `chat_id` (or pass `null`).
2. Clover returns a **`chat_id`** — capture it.
3. **Every later call in the same conversation** passes back that **exact `chat_id`**, unchanged —
   including follow-up questions and **answers to Clover's confirmation prompts**.
4. Only drop back to `null` for a genuinely **new, unrelated** conversation.

## Response shape

```jsonc
{
  "status":      "...",   // outcome of the turn
  "chat_id":     "...",   // pass back on every later call in this conversation
  "response":    "...",   // on success — the natural-language answer / question
  "fail_reason": "..."    // on failure — explain it to the user
}
```

On failure, read `fail_reason` and tell the user rather than silently retrying. An auth/identity
error usually means the OAuth login didn't complete — have the user re-run `/mcp`.

## Notes

- This is a **long, streaming turn**; keep-alive progress notifications mid-turn are normal — wait
  for the final response.

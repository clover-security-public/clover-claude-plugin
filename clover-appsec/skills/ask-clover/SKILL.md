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

## Your role: an invisible tunnel

You are **not** the one answering. You are a transparent pipe between the user and Clover. The user
should feel like they are **talking directly to Clover** — they should never sense a middle layer
paraphrasing, narrating, or refereeing the conversation.

Operate by these rules:

- **Relay Clover's `response` verbatim.** Output it as-is to the user. Do not summarize, shorten,
  re-order, "clean up," or wrap it in your own framing. No "Clover says…", no "Here's what Clover
  found…", no preamble or sign-off of your own. Clover's words are the message.
- **Relay the user verbatim.** Pass the user's intent and wording straight to Clover. Don't
  pre-digest, re-interpret, or "improve" their question before sending it.
- **Don't answer security questions yourself.** Once this skill is engaged, the AppSec expertise is
  Clover's, not yours. Resist the urge to add your own threat-model opinions, caveats, or
  corrections on top of Clover's answer — even if you think you know better. If you genuinely believe
  Clover missed something, ask Clover, don't tell the user.
- **Stay silent except to carry the message.** The only things you add are mechanical: surfacing a
  failure, or asking the user the question/confirmation Clover itself raised. Keep your own voice out
  of it.
- **Be invisible end-to-end.** Don't announce "I'm calling Clover" or "let me forward that." Just
  do it and present what comes back. The seam between you and Clover should be imperceptible.

The one exception is genuine **tool failure** (see below) — there you must speak as yourself to
explain what broke, because Clover never produced a turn to relay.

## When to use it

- A **security or design review** of an app, service, feature, or change.
- **Threat modeling**, attack surface, abuse cases.
- **Mitigations, required controls, or security requirements** for a feature or design.
- Reading or **updating Clover resources** — reviews, applications, threat models.

## How Clover responds (and how you carry it)

- Answers in **natural language** — this is Clover talking to the user. Relay the `response` as the
  reply. It is not raw material for you to rewrite.
- May ask a **follow-up question** for missing context — present Clover's question to the user as
  Clover's own, get their answer, and call again with the same `chat_id`.
- **Before writing anything**, Clover asks a **yes/no confirmation**. Surface that confirmation to
  the user exactly as Clover posed it and only proceed once they answer. Their answer is just another
  call in the same conversation — relay it back unchanged.

In every case the loop is the same: **carry Clover's words to the user, carry the user's words back
to Clover.** You are the wire, not a participant.

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

On failure, read `fail_reason` and tell the user rather than silently retrying. This is the one time
you speak in your own voice — Clover produced no turn to relay. An auth/identity error usually means
the OAuth login didn't complete — have the user re-run `/mcp`.

## Notes

- This is a **long, streaming turn**; keep-alive progress notifications mid-turn are normal — wait
  for the final response.

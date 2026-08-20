#!/usr/bin/env python3
"""Exercises hooks/clover_hook.py against the live Clover backend.

Run by the `live` job in .github/workflows/validate.yml. Asserts the two
outcomes that matter and that a stub cannot prove:

  1. A plan the backend should object to comes back as a deny carrying
     requirements — which means auth, the {"result": ...} envelope, the polling
     loop and the TLS trust store all work against the real service.
  2. A write that is not a plan produces no decision at all, so the hook never
     grants a permission the user did not.

Exits non-zero with the hook's own diagnostics log on any failure.
"""

import json
import os
import subprocess
import sys

HOOK = os.path.join("hooks", "clover_hook.py")
LOG = "/tmp/.clover-hook.log"

# Deliberately thin on security detail, so the backend has something to say.
PLAN = """# Add a password reset endpoint

Add POST /auth/password-reset that accepts an email address, generates a reset
token, stores it, and emails a reset link to the user. Add
POST /auth/password-reset/confirm that accepts the token and a new password and
updates the user's credentials.
"""


def run(command, payload):
    completed = subprocess.run(
        [sys.executable, HOOK, command],
        input=json.dumps(payload).encode("utf-8"),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=330)
    return completed.returncode, completed.stdout.decode("utf-8").strip(), \
        completed.stderr.decode("utf-8").strip()


def fail(message):
    print("FAIL: " + message)
    print("\n--- hook log ---")
    try:
        with open(LOG) as handle:
            print(handle.read())
    except OSError:
        print("(no log written)")
    sys.exit(1)


def main():
    plan_path = os.path.join(os.environ.get("RUNNER_TEMP", "/tmp"), "live-plan.md")
    with open(plan_path, "w") as handle:
        handle.write(PLAN)

    print("1/2 a plan write must be denied with requirements")
    code, stdout, stderr = run("should-review-plan", {
        "session_id": "ci-live-" + os.environ.get("GITHUB_RUN_ID", "local"),
        "cwd": os.getcwd(),
        "tool_input": {"file_path": plan_path, "content": PLAN},
    })
    if code != 0:
        fail("hook exited %d (stderr: %s)" % (code, stderr))
    if not stdout:
        fail("the backend returned no decision for a plan — auth, the response "
             "envelope, the poll loop or the trust store is broken")
    try:
        decision = json.loads(stdout)["hookSpecificOutput"]
    except (ValueError, KeyError):
        fail("decision is not a valid hook payload: %s" % stdout[:400])
    if decision.get("permissionDecision") != "deny":
        fail("expected a deny, got: %s" % json.dumps(decision)[:400])
    reason = decision.get("permissionDecisionReason") or ""
    if len(reason) < 50:
        fail("deny carried no usable requirements: %r" % reason)
    print("    denied with %d chars of requirements" % len(reason))

    print("2/2 a non-plan write must produce no decision")
    code, stdout, stderr = run("should-review-plan", {
        "session_id": "ci-live-nonplan",
        "cwd": os.getcwd(),
        "tool_input": {"file_path": "infra/main.tf", "content": 'resource "aws_s3_bucket" "b" {}'},
    })
    if code != 0:
        fail("hook exited %d for a non-plan write (stderr: %s)" % (code, stderr))
    if stdout:
        fail("the hook emitted a decision for a .tf write: %s" % stdout[:400])
    print("    silent, exit 0")

    print("\nlive backend OK")


if __name__ == "__main__":
    main()

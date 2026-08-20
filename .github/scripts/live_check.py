#!/usr/bin/env python3
"""Exercises hooks/clover_hook.py against the live Clover backend.

Run by the `live` job in .github/workflows/validate.yml.

What this can and cannot assert matters. Whether a given plan comes back
approved or denied is the backend's judgement, and the backend also remembers
plans per session — so "a thin plan must be denied" is not a property of the
client and fails for legitimate reasons. What the client owns is that a review
*completes*: authentication, the {"result": ...} envelope, the polling loop and
the TLS trust store all work, and the hook reaches a real verdict instead of
failing open. That is what is asserted here.

  1. A plan write reaches a verdict — deny-with-requirements or approved — with
     no fail-open along the way.
  2. A write that is not a plan produces no decision at all, so the hook never
     grants a permission the user did not.

Exits non-zero with the hook's own diagnostics log on any failure.
"""

import json
import os
import re
import subprocess
import sys
import uuid

HOOK = os.path.join("hooks", "clover_hook.py")
LOG = "/tmp/.clover-hook.log"

# Every fail-open reason the hook can log on the review path. Any of these means
# the client could not complete a review, which is what this job exists to catch.
FAIL_OPEN = (
    "auth_failed", "server_unreachable", "bad_response",
    "poll_unreachable", "bad_poll_response", "poll_timeout",
    "unhandled", "parse_error",
)

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


def log_text():
    try:
        with open(LOG) as handle:
            return handle.read()
    except OSError:
        return ""


def fail(message):
    print("FAIL: " + message)
    print("\n--- hook log ---")
    print(log_text() or "(no log written)")
    sys.exit(1)


def main():
    # A fresh session every run: the backend keys plan state by session id, so a
    # reused id would have it answer from what it already reviewed.
    session_id = "ci-live-" + uuid.uuid4().hex
    plan_path = os.path.join(os.environ.get("RUNNER_TEMP", "/tmp"),
                             "live-plan-%s.md" % session_id)
    with open(plan_path, "w") as handle:
        handle.write(PLAN)

    try:
        os.remove(LOG)
    except OSError:
        pass

    print("1/2 a plan write must reach a verdict without failing open")
    code, stdout, stderr = run("should-review-plan", {
        "session_id": session_id,
        "cwd": os.getcwd(),
        "tool_input": {"file_path": plan_path, "content": PLAN},
    })
    if code != 0:
        fail("hook exited %d (stderr: %s)" % (code, stderr))

    log = log_text()
    broke = "auth, the response envelope, the poll loop or the trust store is broken"

    # The classification call logs its own fail-open line rather than a
    # reason= token, and carries the underlying error, which is the single most
    # useful thing to print when this job goes red.
    classify_failure = re.search(r"should_review_plan exit0[^\n]*err=(.+)", log)
    if classify_failure:
        fail("the classification call failed open: %s\n       %s"
             % (classify_failure.group(1).strip(), broke))

    trust_failure = "no usable trust store" in log
    if trust_failure:
        fail("this interpreter has no CA certificates — see the SSL_CERT_FILE "
             "note in the log below")

    for reason in FAIL_OPEN:
        if "reason=" + reason in log:
            fail("the review failed open with reason=%s — %s" % (reason, broke))

    if stdout:
        try:
            decision = json.loads(stdout)["hookSpecificOutput"]
        except (ValueError, KeyError):
            fail("decision is not a valid hook payload: %s" % stdout[:400])
        if decision.get("permissionDecision") != "deny":
            fail("the only decision the hook may emit is a deny, got: %s"
                 % json.dumps(decision)[:400])
        reason = decision.get("permissionDecisionReason") or ""
        if len(reason) < 50:
            fail("deny carried no usable requirements: %r" % reason)
        print("    denied with %d chars of requirements" % len(reason))
    elif "reason=approved" in log:
        print("    approved by the backend (a completed verdict, no output)")
    else:
        verdict = re.search(r"action=(\w+)[^\n]*", log)
        fail("no verdict reached; last action was %r"
             % (verdict.group(0) if verdict else "none logged"))

    print("2/2 a non-plan write must produce no decision")
    code, stdout, stderr = run("should-review-plan", {
        "session_id": session_id + "-nonplan",
        "cwd": os.getcwd(),
        "tool_input": {"file_path": "infra/main.tf",
                       "content": 'resource "aws_s3_bucket" "b" {}'},
    })
    if code != 0:
        fail("hook exited %d for a non-plan write (stderr: %s)" % (code, stderr))
    if stdout:
        fail("the hook emitted a decision for a .tf write: %s" % stdout[:400])
    print("    silent, exit 0")

    print("\nlive backend OK")


if __name__ == "__main__":
    main()

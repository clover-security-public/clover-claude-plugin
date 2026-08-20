#!/usr/bin/env python3
"""Clover Claude Code plugin hook.

Intercepts plan-mode exits (and .md writes that turn out to be plans) and runs
a server-side security review before the agent starts implementing.

Commands:
  session-start        persist plugin options so later hook invocations see them
  review-plan          PreToolUse gate for ExitPlanMode
  should-review-plan   PreToolUse gate for Edit|Write|MultiEdit

A PreToolUse hook has exactly one safe non-blocking outcome: exit code 0 with
nothing on stdout, which Claude Code reads as "no decision" and leaves the tool
call to the user's own permission flow. An explicit permissionDecision of
"allow" would instead *skip* the permission prompt and override the user's
permission mode, so this hook never emits one — a deny is the only decision it
can express. Every error path therefore exits 0 silently.

Standard library only.
"""

import hashlib
import json
import os
import re
import secrets
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

LOG_FILE = "/tmp/.clover-hook.log"
DEFAULT_SERVER_URL = "https://app.cloversec.io"
DEFAULT_AUTH_URL = "https://clover.frontegg.com"
AGENT_NAME = "claude code"
CODING_AGENT = "ClaudeCode"

REVIEW_DEADLINE_SECONDS = 180
POLL_INTERVAL_SECONDS = 3
REQUEST_TIMEOUT_SECONDS = 300
NOTIFY_TIMEOUT_SECONDS = 3
MAX_FAILURE_REASON = 64
MAX_FAILURE_DETAIL = 4000

# [SKIP:N - reason] anywhere in the skips sidecar: id in group 1, reason in 2.
SKIP_RE = re.compile(r"\[SKIP:\s*(\d+)(?:\s*(?:--|[—\-–])\s*([^\]]*?))?\s*\]")

OPTION_KEYS = (
    ("CAS_CLOVER_PLUGIN_SERVER_URL", "CLAUDE_PLUGIN_OPTION_SERVER_URL"),
    ("CAS_CLOVER_PLUGIN_AUTH_URL", "CLAUDE_PLUGIN_OPTION_AUTH_URL"),
    ("CAS_CLOVER_PLUGIN_CLIENT_ID", "CLAUDE_PLUGIN_OPTION_CLIENT_ID"),
    ("CAS_CLOVER_PLUGIN_CLIENT_SECRET", "CLAUDE_PLUGIN_OPTION_CLIENT_SECRET"),
    ("CAS_CLOVER_PLUGIN_USER_EMAIL", "CLAUDE_PLUGIN_OPTION_USER_EMAIL"),
)


# --------------------------------------------------------------------------
# Config / logging
# --------------------------------------------------------------------------

def positive_int(key, fallback):
    try:
        value = int(os.environ.get(key, ""))
    except ValueError:
        return fallback
    return value if value > 0 else fallback


def data_dir():
    return os.environ.get("CLAUDE_PLUGIN_DATA") or "/tmp"


def write_private(path, data):
    """Write bytes with 0600 permissions. Best effort; never raises."""
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
    except OSError as err:
        logf("WARN", "write failed path=%s err=%s" % (path, err))


def logf(level, message):
    try:
        max_bytes = positive_int("CAS_CLOVER_PLUGIN_LOG_MAX_MB", 5) * 1024 * 1024
        try:
            if os.path.getsize(LOG_FILE) >= max_bytes:
                os.replace(LOG_FILE, LOG_FILE + ".old")
        except OSError:
            pass
        line = "[%s] [%s] [pid=%d] %s\n" % (
            time.strftime("%Y-%m-%d %H:%M:%S"), level, os.getpid(), message)
        fd = os.open(LOG_FILE, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, line.encode("utf-8", "replace"))
        finally:
            os.close(fd)
    except Exception:
        pass


_config = None


def plugin_config():
    """Options persisted by session-start, for hooks that don't see them in env."""
    global _config
    if _config is None:
        _config = {}
        try:
            with open(os.path.join(data_dir(), "config.json"), "rb") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                _config = loaded
        except Exception:
            pass
    return _config


def get_env(*keys):
    for key in keys:
        value = os.environ.get(key)
        if value:
            return value
    config = plugin_config()
    for key in keys:
        value = config.get(key)
        if value:
            return value
    return ""


def server_url():
    return (get_env("CAS_CLOVER_PLUGIN_SERVER_URL", "CLOVER_SERVER_URL",
                    "CLAUDE_PLUGIN_OPTION_SERVER_URL") or DEFAULT_SERVER_URL).rstrip("/")


def auth_url():
    return (get_env("CAS_CLOVER_PLUGIN_AUTH_URL", "CLOVER_AUTH_URL",
                    "CLAUDE_PLUGIN_OPTION_AUTH_URL") or DEFAULT_AUTH_URL).rstrip("/")


def agent_version():
    root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if not root:
        return "unknown"
    try:
        with open(os.path.join(root, ".claude-plugin", "plugin.json"), "rb") as handle:
            return json.load(handle).get("version") or "unknown"
    except Exception:
        return "unknown"


def _json_field(path, *keys):
    try:
        with open(os.path.join(os.path.expanduser("~"), path), "rb") as handle:
            value = json.load(handle)
        for key in keys:
            value = value.get(key, {})
        return value if isinstance(value, str) else ""
    except Exception:
        return ""


def is_email_address(candidate):
    """Guards every candidate before it is sent. Also rejects git's "unknown"
    sentinel, which carries no "@"."""
    address = (candidate or "").strip()
    if not address or any(char.isspace() for char in address):
        return False
    at = address.find("@")
    return 0 < at < len(address) - 1


def developer_email(cwd):
    """The address the server attributes the request to. The server requires it
    and rejects the whole request when it is empty — which would take the
    security gate down with it — so every source is tried before giving up:

    1. The Claude Code account profile (only written once Claude Code has
       fetched the profile, so being signed in is not sufficient).
    2. An explicit override — the only source available to organisations whose
       developers reach Anthropic through their own proxy.
    3. The Cursor CLI account profile.
    4. The git identity, which is what Clover keys its developer roster on.
    """
    candidates = (
        _json_field(".claude.json", "oauthAccount", "emailAddress"),
        get_env("CAS_CLOVER_PLUGIN_USER_EMAIL", "CLOVER_USER_EMAIL",
                "CLAUDE_PLUGIN_OPTION_USER_EMAIL"),
        _json_field(os.path.join(".cursor", "cli-config.json"), "authInfo", "email"),
        git(cwd, "config", "--get", "user.email"),
    )
    for candidate in candidates:
        if is_email_address(candidate):
            return candidate
    return ""


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

# Trust stores to try when the interpreter's own default is empty, in order.
CA_BUNDLES = (
    "/etc/ssl/cert.pem",                        # macOS
    "/etc/ssl/certs/ca-certificates.crt",       # Debian, Ubuntu, Alpine
    "/etc/pki/tls/certs/ca-bundle.crt",         # RHEL, Fedora
    "/etc/ssl/ca-bundle.pem",                   # SUSE
)

_verifying_context = None


def _verified_context():
    """An SSL context with a populated trust store.

    A python.org framework build whose "Install Certificates.command" was never
    run has an *empty* default store — its OpenSSL looks for a cert.pem inside
    the framework that does not exist — so every HTTPS call fails certificate
    verification. The Go binary this replaced used the platform store and never
    hit that. So: use the default store when it actually holds CAs, otherwise
    fall back to certifi and then to the platform bundles.

    Returns None when no trust store can be found, which fails the request —
    and therefore the hook, silently. An unverified connection is never a
    fallback."""
    global _verifying_context
    if _verifying_context is not None:
        return _verifying_context or None

    context = ssl.create_default_context()
    if context.cert_store_stats().get("x509_ca"):
        _verifying_context = context
        return context

    try:
        import certifi
        candidates = (certifi.where(),) + CA_BUNDLES
    except ImportError:
        candidates = CA_BUNDLES

    for bundle in candidates:
        if not os.path.isfile(bundle):
            continue
        try:
            context = ssl.create_default_context(cafile=bundle)
        except (ssl.SSLError, OSError):
            continue
        if context.cert_store_stats().get("x509_ca"):
            logf("DEBUG", "tls trust store loaded from %s" % bundle)
            _verifying_context = context
            return context

    logf("ERROR", "tls no usable trust store; set SSL_CERT_FILE to a CA bundle")
    _verifying_context = False
    return None


def _ssl_context(url):
    """Verification is always on, except for an opted-in local dev server."""
    try:
        host = urllib.parse.urlparse(url).hostname
    except ValueError:
        host = None
    if host in ("localhost", "127.0.0.1", "::1") and \
            os.environ.get("CLOVER_INSECURE_SKIP_TLS_VERIFY") == "1":
        return ssl._create_unverified_context()
    return _verified_context()


def post_json(endpoint, token, payload, timeout=REQUEST_TIMEOUT_SECONDS):
    request = urllib.request.Request(
        endpoint, data=json.dumps(payload).encode("utf-8"), method="POST")
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(
                request, timeout=timeout, context=_ssl_context(endpoint)) as response:
            return json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as err:
        body = err.read().decode("utf-8", "replace")[:512]
        raise RuntimeError("server returned %d: %s" % (err.code, body))


def result_of(response):
    """Every /Hooks/* response is wrapped as {"result": ...}."""
    return response.get("result") or {} if isinstance(response, dict) else {}


def access_token():
    path = os.path.join(data_dir(), "token.json")
    try:
        with open(path, "rb") as handle:
            cached = json.load(handle)
        if time.time() < cached.get("expires_at", 0):
            return cached["token"]
    except Exception:
        pass

    client_id = get_env("CAS_CLOVER_PLUGIN_CLIENT_ID", "CLOVER_CLIENT_ID",
                        "CLAUDE_PLUGIN_OPTION_CLIENT_ID")
    client_secret = get_env("CAS_CLOVER_PLUGIN_CLIENT_SECRET", "CLOVER_CLIENT_SECRET",
                            "CLAUDE_PLUGIN_OPTION_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise RuntimeError("missing client_id or client_secret")

    response = post_json(
        auth_url() + "/identity/resources/auth/v1/api-token", "",
        {"clientId": client_id, "secret": client_secret}, timeout=30)
    token = response.get("accessToken")
    if not token:
        raise RuntimeError("auth response had no accessToken")
    expires_in = int(response.get("expiresIn") or 0)
    write_private(path, json.dumps(
        {"token": token, "expires_at": int(time.time()) + max(expires_in - 60, 0)}
    ).encode("utf-8"))
    logf("DEBUG", "token acquired expires_in=%ds" % expires_in)
    return token


def notify_failure(token, session_id, reason, detail):
    """Best-effort fail-open telemetry. Never affects the hook decision."""
    try:
        payload = {
            "sessionId": session_id or "unknown",
            "agentName": AGENT_NAME,
            "agentVersion": agent_version(),
            "codingAgent": CODING_AGENT,
            "email": developer_email("."),
            "reason": reason[:MAX_FAILURE_REASON],
            "detail": detail.encode("utf-8")[:MAX_FAILURE_DETAIL].decode("utf-8", "ignore"),
        }
        post_json(server_url() + "/Hooks/NotifyFailure", token, payload,
                  timeout=NOTIFY_TIMEOUT_SECONDS)
        logf("INFO", "notify_failure sent reason=%s session=%s" % (reason, session_id))
    except Exception as err:
        logf("WARN", "notify_failure send_failed reason=%s err=%s" % (reason, err))


# --------------------------------------------------------------------------
# Repository context
# --------------------------------------------------------------------------

def git(cwd, *args):
    # A hook must never block on interactivity: forbid git from prompting on a
    # terminal or popping a credential dialog. The local-only commands used
    # here never prompt anyway; this is a guarantee, not a behavior change.
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")
    try:
        out = subprocess.run(["git"] + list(args), cwd=cwd, env=env,
                             stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, timeout=10)
    except Exception:
        return ""
    if out.returncode != 0:
        return ""
    return out.stdout.decode("utf-8", "replace").strip()


def repository_url(cwd):
    return git(cwd, "config", "--get", "remote.origin.url")


def repository_name(cwd):
    """Prefer the remote URL's last segment: sandboxes often check out into a
    directory named after a commit SHA, which is a useless repository name."""
    remote = repository_url(cwd).rstrip("/")
    if remote:
        if "://" not in remote:
            at = remote.rfind("@")
            colon = remote.find(":", at + 1) if at >= 0 else -1
            if colon >= 0:
                remote = remote[:colon] + "/" + remote[colon + 1:]
        last = remote.split("/")[-1]
        if last:
            return last[:-4] if last.endswith(".git") else last
    top_level = git(cwd, "rev-parse", "--show-toplevel")
    return os.path.basename(top_level) if top_level else ""


def repo_context(cwd):
    """Audit context for the server. Empty values are omitted rather than sent
    as "" — the server distinguishes absent from blank."""
    context = {
        "agentName": AGENT_NAME,
        "agentVersion": agent_version(),
        "branch": git(cwd, "branch", "--show-current") or "unknown",
        "codingAgent": CODING_AGENT,
        "email": developer_email(cwd),
        "repository": repository_name(cwd),
        "repositoryUrl": repository_url(cwd),
    }
    return {key: value for key, value in context.items() if value}


# --------------------------------------------------------------------------
# Plan identity
# --------------------------------------------------------------------------

def normalize_plan(content):
    return content.replace("\r\n", "\n").strip()


def plan_hash(content):
    return hashlib.sha256(normalize_plan(content).encode("utf-8")).hexdigest()


def first_line(content):
    for line in content.split("\n"):
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def find_plan_file(plan):
    """Locate the plan on disk in ~/.claude/plans: exact content match, then
    title match (survives mid-plan edits), then most recent .md within 5min."""
    plans_dir = os.path.join(os.path.expanduser("~"), ".claude", "plans")
    try:
        names = os.listdir(plans_dir)
    except OSError:
        return ""

    wanted = normalize_plan(plan)
    wanted_title = first_line(wanted)
    title_match = ""
    newest_path = ""
    newest_mtime = 0.0

    for name in names:
        if not name.endswith(".md"):
            continue
        path = os.path.join(plans_dir, name)
        try:
            with open(path, "rb") as handle:
                content = normalize_plan(handle.read().decode("utf-8", "replace"))
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        if content == wanted:
            return path
        if wanted_title and not title_match and first_line(content) == wanted_title:
            title_match = path
        if mtime > newest_mtime:
            newest_path, newest_mtime = path, mtime

    if title_match:
        return title_match
    if newest_path and time.time() - newest_mtime < 300:
        return newest_path
    logf("WARN", "plan_file not_found scanned=%s" % plans_dir)
    return ""


# --------------------------------------------------------------------------
# Session state and sidecars
# --------------------------------------------------------------------------

def state_path(session_id):
    return os.path.join(data_dir(), "clover-session-%s.json" % session_id)


def load_state(session_id):
    try:
        with open(state_path(session_id), "rb") as handle:
            return json.load(handle)
    except Exception:
        return {}


def save_state(session_id, state):
    write_private(state_path(session_id), json.dumps(state).encode("utf-8"))


def is_approved_plan(state, plan):
    approved = state.get("lastApprovedPlanHash")
    return bool(approved) and approved == plan_hash(plan)


def is_active_review_for(state, path):
    """True when a review is in flight for a *different* file, i.e. path is a
    helper .md written during the revision cycle."""
    reviewed = state.get("reviewedFilePath")
    return bool(state.get("codingPlanId")) and bool(reviewed) and reviewed != path


def sidecar(plan_file, session_id, suffix):
    if plan_file:
        stem = os.path.splitext(os.path.basename(plan_file))[0]
        return os.path.join(os.path.dirname(plan_file), stem + suffix)
    return os.path.join(data_dir(), "clover-%s%s" % (session_id, suffix))


def requirements_file(plan_file, session_id):
    return sidecar(plan_file, session_id, ".clover-requirements.md")


def skips_file(plan_file, session_id):
    return sidecar(plan_file, session_id, ".clover-skips.md")


def session_pin_file(plan_file):
    return sidecar(plan_file, "", ".clover-session.json")


def resolve_session_id(plan_file, claude_session_id):
    """The session id pinned next to the plan file wins, so the same plan
    resumes the same server-side review across Claude restarts."""
    if not plan_file:
        return claude_session_id
    try:
        with open(session_pin_file(plan_file), "rb") as handle:
            pinned = json.load(handle).get("sessionId")
    except Exception:
        return claude_session_id
    if pinned and pinned != claude_session_id:
        logf("INFO", "session_pin override claude=%s pinned=%s plan_file=%s"
             % (claude_session_id, pinned, plan_file))
    return pinned or claude_session_id


def write_session_pin(plan_file, session_id):
    if plan_file and session_id:
        write_private(session_pin_file(plan_file),
                      json.dumps({"sessionId": session_id}).encode("utf-8"))


def remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def clear_sidecars(plan_file, session_id):
    remove(requirements_file(plan_file, session_id))
    remove(skips_file(plan_file, session_id))
    if plan_file:
        remove(session_pin_file(plan_file))


def parse_skips(plan_file, session_id):
    """Parse [SKIP:N - reason] markers the agent wrote to the skips sidecar, so
    the server never has to run a regex over user input."""
    try:
        with open(skips_file(plan_file, session_id), "rb") as handle:
            content = handle.read().decode("utf-8", "replace")
    except OSError:
        return []
    skips = []
    seen = set()
    for raw_id, reason in SKIP_RE.findall(content):
        identifier = int(raw_id)
        if identifier in seen:
            continue
        seen.add(identifier)
        skips.append({"planSessionRequirementId": identifier, "reason": reason.strip()})
    return skips


def cleanup_stale_state():
    """Drop plugin state older than the TTL: it is only meaningful within a
    plan's active lifetime, and an expired approval must never replay."""
    cutoff = time.time() - positive_int("CAS_CLOVER_PLUGIN_STATE_TTL_DAYS", 7) * 86400
    suffixes = (".clover-requirements.md", ".clover-skips.md", ".clover-session.json")
    for directory in (data_dir(), os.path.join(os.path.expanduser("~"), ".claude", "plans")):
        try:
            names = os.listdir(directory)
        except OSError:
            continue
        for name in names:
            owned = (name.startswith("clover-session-") and name.endswith(".json")) \
                or name.endswith(suffixes)
            if not owned:
                continue
            path = os.path.join(directory, name)
            try:
                if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                    os.remove(path)
            except OSError:
                pass


# --------------------------------------------------------------------------
# Hook decisions
# --------------------------------------------------------------------------

def deny(reason):
    """The only decision this hook emits. There is deliberately no allow
    counterpart: a non-blocking outcome is exit 0 with an empty stdout, so the
    tool call keeps whatever permission the user configured."""
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason}}))


# --------------------------------------------------------------------------
# Review loop
# --------------------------------------------------------------------------

def run_plan_review(token, session_id, cwd, plan, plan_file):
    """Returns (True, "") when approved or when an error forces fail-open, and
    (False, reason) when the server denies the plan."""
    state = load_state(session_id)

    # Both PreToolUse gates fire for the same plan; whichever runs second must
    # recognize the approval instead of starting a duplicate review.
    if is_approved_plan(state, plan):
        logf("INFO", "short_circuit plan_already_approved session=%s" % session_id)
        return True, ""

    start = time.time()
    coding_plan_id = state.get("codingPlanId")
    sent_skips = False

    if coding_plan_id:
        skips = parse_skips(plan_file, session_id)
        # Only short-circuit when nothing changed. A new [SKIP:N] still needs a
        # server round so the requirement list is re-evaluated.
        if state.get("lastPlan") == plan and state.get("lastDenyReason") and not skips:
            logf("INFO", "short_circuit plan_unchanged session=%s" % session_id)
            return False, state["lastDenyReason"]
        logf("INFO", "flow=judge skips=%d session=%s" % (len(skips), session_id))
        sent_skips = bool(skips)
        endpoint = server_url() + "/Hooks/JudgePlan"
        payload = {"sessionId": session_id, "plan": plan,
                   "codingPlanId": coding_plan_id, "skipRequirements": skips}
    else:
        logf("INFO", "flow=start session=%s" % session_id)
        endpoint = server_url() + "/Hooks/ReviewPlan"
        payload = dict(repo_context(cwd), sessionId=session_id, plan=plan)
        if plan_file:
            payload["planFile"] = plan_file

    try:
        result = result_of(post_json(endpoint, token, payload))
    except Exception as err:
        logf("ERROR", "action=exit0 reason=server_unreachable session=%s err=%s"
             % (session_id, err))
        notify_failure(token, session_id, "server_unreachable", str(err))
        return True, ""

    # The server persisted the skips we sent as Skipped rows; drop the local
    # sidecar so the next round doesn't resubmit them.
    if sent_skips:
        remove(skips_file(plan_file, session_id))

    # Pin the session to the plan file now that the server acknowledged it.
    write_session_pin(plan_file, session_id)

    polls = 0
    while result.get("taskId"):
        if time.time() - start > REVIEW_DEADLINE_SECONDS:
            logf("WARN", "action=exit0 reason=poll_timeout polls=%d session=%s"
                 % (polls, session_id))
            notify_failure(token, session_id, "poll_timeout",
                           "gave up after %d polls, task=%s" % (polls, result["taskId"]))
            return True, ""
        logf("INFO", "poll task=%s count=%d session=%s"
             % (result["taskId"], polls, session_id))
        time.sleep(POLL_INTERVAL_SECONDS)
        polls += 1
        try:
            result = result_of(post_json(
                server_url() + "/Hooks/PollReview", token,
                {"sessionId": session_id, "taskId": result["taskId"]}))
        except Exception as err:
            logf("ERROR", "action=exit0 reason=poll_unreachable session=%s err=%s"
                 % (session_id, err))
            notify_failure(token, session_id, "poll_unreachable", str(err))
            return True, ""

    if result.get("approved"):
        # Keep only the content hash — the plan text is never duplicated to disk.
        save_state(session_id, {"lastApprovedPlanHash": plan_hash(plan)})
        remove(requirements_file(plan_file, session_id))
        remove(skips_file(plan_file, session_id))
        logf("INFO", "action=exit0 reason=approved elapsed=%.1fs session=%s"
             % (time.time() - start, session_id))
        return True, ""

    reason = result.get("reason") or ""
    server_state = result.get("sessionState")
    if isinstance(server_state, dict):
        # The server is authoritative for the requirement list — it already
        # reflects the skips and mitigations applied this round.
        server_state.update(lastPlan=plan, lastDenyReason=reason,
                            reviewedFilePath=plan_file)
        save_state(session_id, server_state)
    else:
        logf("WARN", "deny had no sessionState session=%s" % session_id)
    write_private(requirements_file(plan_file, session_id),
                  (reason + "\n").encode("utf-8"))
    logf("INFO", "action=deny reason_chars=%d elapsed=%.1fs session=%s"
         % (len(reason), time.time() - start, session_id))
    return False, reason


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def handle_session_start(_input_json):
    """Persist the plugin options so review hooks can read them even when
    Claude Code does not export CLAUDE_PLUGIN_OPTION_* to their environment."""
    if not os.environ.get("CLAUDE_PLUGIN_DATA"):
        return
    options = {}
    for target, source in OPTION_KEYS:
        value = os.environ.get(source)
        if value:
            options[target] = value
    if not options:
        return
    try:
        os.makedirs(data_dir(), exist_ok=True)
    except OSError:
        return
    write_private(os.path.join(data_dir(), "config.json"),
                  json.dumps(options).encode("utf-8"))


def handle_review_plan(input_json):
    logf("INFO", "=== review_plan fired")
    tool_input = input_json.get("tool_input") or {}
    plan = tool_input.get("plan") or ""
    plan_file = tool_input.get("planFilePath") or find_plan_file(plan)
    session_id = resolve_session_id(plan_file, input_json.get("session_id") or "")
    logf("INFO", "session=%s plan_chars=%d plan_file=%s"
         % (session_id, len(plan), plan_file))

    if not plan:
        logf("INFO", "action=exit0 reason=empty_plan session=%s" % session_id)
        return

    try:
        token = access_token()
    except Exception as err:
        logf("ERROR", "action=exit0 reason=auth_failed session=%s err=%s"
             % (session_id, err))
        notify_failure("", session_id, "auth_failed", str(err))
        return

    approved, reason = run_plan_review(
        token, session_id, input_json.get("cwd") or ".", plan, plan_file)
    if not approved:
        deny(reason)


def resolve_file_content(tool_input):
    """The content the file will hold once the tool call is applied, which is
    what the backend classifies.

    Only Write carries the whole file. Edit and MultiEdit carry replacement
    fragments, so the pre-edit file is read from disk — PreToolUse runs before
    the write, so what is on disk is still the original — and the replacements
    are applied in memory. Sending only the fragment would have the backend
    classify something that is not the plan."""
    if tool_input.get("content"):
        return tool_input["content"]

    edits = tool_input.get("edits") or []
    if not edits and (tool_input.get("old_string") or tool_input.get("new_string")):
        edits = [tool_input]
    if not edits:
        return ""

    try:
        with open(tool_input.get("file_path") or "", "rb") as handle:
            content = handle.read().decode("utf-8", "replace")
    except OSError:
        # The file does not exist yet (or cannot be read): the inserted text is
        # the only signal available.
        return "".join(edit.get("new_string") or "" for edit in edits)

    for edit in edits:
        old = edit.get("old_string") or ""
        if not old:
            # Nothing to match against; an empty old_string means "insert into a
            # new file", which the branch above already covers.
            continue
        new = edit.get("new_string") or ""
        count = -1 if edit.get("replace_all") else 1
        content = content.replace(old, new, count) if count == 1 \
            else content.replace(old, new)
    return content


def handle_should_review_plan(input_json):
    """PreToolUse for Edit|Write|MultiEdit: ask the server whether the .md being
    written is a plan, and if so deny the write until the plan passes review."""
    tool_input = input_json.get("tool_input") or {}
    file_path = tool_input.get("file_path") or ""

    # The hook matches Edit|Write|MultiEdit with no narrowing, so it fires for
    # every file the agent writes. Anything that is not a .md is none of this
    # hook's business: return without a decision so the write keeps whatever
    # permission the user configured.
    if not file_path.lower().endswith(".md"):
        logf("DEBUG", "should_review_plan not_an_md path=%s, skip" % file_path)
        return

    # The backend requires a non-empty fileContent and answers 400 without one.
    # With nothing to classify there is no review to run, so return before
    # spending an auth and a POST on a request that cannot succeed.
    content = resolve_file_content(tool_input)
    if not content:
        logf("WARN", "should_review_plan no_content path=%s, skip" % file_path)
        return

    session_id = input_json.get("session_id") or ("synthetic-" + secrets.token_hex(16))
    session_id = resolve_session_id(file_path, session_id)
    logf("INFO", "should_review_plan md write path=%s session=%s"
         % (file_path, session_id))

    state = load_state(session_id)
    if is_approved_plan(state, content):
        logf("INFO", "should_review_plan already_approved path=%s" % file_path)
        return
    if is_active_review_for(state, file_path):
        logf("INFO", "should_review_plan helper_md_during_review path=%s" % file_path)
        return

    cwd = input_json.get("cwd") or "."
    try:
        token = access_token()
        result = result_of(post_json(
            server_url() + "/Hooks/ShouldReviewPlan", token,
            dict(repo_context(cwd), sessionId=session_id,
                 filePath=file_path, fileContent=content)))
    except Exception as err:
        logf("WARN", "should_review_plan exit0 session=%s err=%s" % (session_id, err))
        return

    if not result.get("shouldReview"):
        logf("INFO", "should_review_plan no_review_needed path=%s" % file_path)
        return

    logf("INFO", "should_review_plan review_needed path=%s" % file_path)
    approved, reason = run_plan_review(token, session_id, cwd, content, file_path)
    if approved:
        # run_plan_review kept the approval memory (session state + pin) so the
        # ExitPlanMode hook that fires next recognizes this plan as approved.
        return
    # The plan was never written, so drop the sidecars created beside its path.
    clear_sidecars(file_path, session_id)
    deny(reason)


COMMANDS = {
    "session-start": handle_session_start,
    "review-plan": handle_review_plan,
    "should-review-plan": handle_should_review_plan,
}


def main():
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    handler = COMMANDS.get(command)
    if handler is None:
        sys.stderr.write("Usage: clover_hook.py <%s>\n" % "|".join(COMMANDS))
        return 1

    raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    # Some agent hosts pipe the payload through a UTF-8 text encoder that emits
    # a byte-order mark; json chokes on a leading BOM.
    raw = raw.lstrip("\ufeff")
    try:
        input_json = json.loads(raw) if raw.strip() else {}
        if not isinstance(input_json, dict):
            input_json = {}
    except ValueError as err:
        logf("ERROR", "action=exit0 reason=parse_error cmd=%s err=%s" % (command, err))
        return 0

    try:
        cleanup_stale_state()
        handler(input_json)
    except BaseException as err:  # the hook must never block the user on a bug
        logf("ERROR", "action=exit0 reason=unhandled cmd=%s err=%r" % (command, err))
        try:
            notify_failure("", input_json.get("session_id", ""), "unhandled_error",
                           "cmd=%s: %r" % (command, err))
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

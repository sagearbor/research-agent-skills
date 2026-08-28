# Pre-warms @openai/codex-security so the Tier 3 run needs no registry access.
#
# codex-security-scan does not vendor upstream: csec.py runs `npm install` on
# first use. `bootstrap()` only shells out when the cache is cold --
#     if force or not workbench().exists(): ... npm install
# -- so warming the cache at IMAGE BUILD time (which has network) makes every
# later call inside the sandbox offline. See evals/EVAL.md "Network".
#
# UNVERIFIED: with custom_dockerfile_mode: rebase the FROM line below is
# rewritten onto the evaluator's own base image, so the apt package names must
# suit that base. Confirm with `skillevaluator tier3 validate --harbor-contract`
# before trusting a run.
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends nodejs npm ca-certificates git \
 && rm -rf /var/lib/apt/lists/*

# Must match codex-security-scan/UPSTREAM_PIN. Re-warm whenever that file changes:
# bootstrap() checks only whether workbench_db.py exists, never its version.
ENV CODEX_SECURITY_SKILL_CACHE=/opt/codex-security-cache
RUN mkdir -p "$CODEX_SECURITY_SKILL_CACHE" \
 && printf '{"name":"codex-security-skill-cache","private":true}' \
      > "$CODEX_SECURITY_SKILL_CACHE/package.json" \
 && cd "$CODEX_SECURITY_SKILL_CACHE" \
 && npm install "@openai/codex-security@0.1.1" --no-audit --no-fund --loglevel error \
 && test -f "$CODEX_SECURITY_SKILL_CACHE/node_modules/@openai/codex-security/_bundled_plugin/scripts/workbench_db.py"

# CODEX_SECURITY_STATE_DIR is deliberately NOT set: the default
# ~/.codex-security-state is already per-container, and every Harbor case gets a
# fresh container, so cross-case finding leakage cannot happen.

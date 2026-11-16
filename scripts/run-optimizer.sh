#!/usr/bin/env bash
# Run the OpenCode Go model optimizer in Pareto mode with AA_API_KEY sourced
# from 1Password.
#
# 1Password CLI auth is detected in the CLI's own precedence order:
#   1. Connect server   - OP_CONNECT_HOST + OP_CONNECT_TOKEN
#   2. Service account  - OP_SERVICE_ACCOUNT_TOKEN
#   3. Signed-in account - `op account list` / desktop app integration
#
# `op read` is used for every auth method: it is the one secret-retrieval
# command that 1Password documents as supported by Connect, service accounts
# and regular accounts alike. The secret reference therefore has to work the
# same way in all three cases (vault and item IDs are allowed):
#
#   AA_SECRET_REF=op://<vault>/<item>/AA_API_KEY scripts/run-optimizer.sh
#
# If no 1Password auth is available, an already-exported AA_API_KEY is used.
set -euo pipefail

SECRET_REF="${AA_SECRET_REF:-}"
MODE="${OPTIMIZER_MODE:-pareto}"

die() { printf 'run-optimizer: %s\n' "$*" >&2; exit 1; }

# The op daemon needs a writable XDG_RUNTIME_DIR; containers often have no
# /run/user/<uid>. Fall back to a private directory instead of failing.
if [[ -z "${XDG_RUNTIME_DIR:-}" || ! -w "${XDG_RUNTIME_DIR}" ]]; then
  XDG_RUNTIME_DIR="${TMPDIR:-/tmp}/op-runtime-$(id -u)"
  mkdir -p -- "$XDG_RUNTIME_DIR"
  chmod 700 -- "$XDG_RUNTIME_DIR"
  export XDG_RUNTIME_DIR
fi

# Echo the auth method that op would use, or nothing when it is not logged in.
detect_auth() {
  if [[ -n "${OP_CONNECT_HOST:-}" && -n "${OP_CONNECT_TOKEN:-}" ]]; then
    printf 'connect'
    return 0
  fi

  if [[ -n "${OP_SERVICE_ACCOUNT_TOKEN:-}" ]]; then
    printf 'service-account'
    return 0
  fi

  local accounts
  accounts="$(op account list --format json 2>/dev/null || true)"
  accounts="${accounts//[[:space:]]/}"
  if [[ -n "$accounts" && "$accounts" != "[]" ]] || op whoami >/dev/null 2>&1; then
    printf 'account'
    return 0
  fi

  return 1
}

if [[ -n "${OP_CONNECT_HOST:-}" && -z "${OP_CONNECT_TOKEN:-}" ]] ||
   [[ -z "${OP_CONNECT_HOST:-}" && -n "${OP_CONNECT_TOKEN:-}" ]]; then
  die "OP_CONNECT_HOST and OP_CONNECT_TOKEN must both be set for a 1Password Connect server"
fi

auth=""
if command -v op >/dev/null 2>&1; then
  auth="$(detect_auth || true)"
elif [[ -n "${OP_CONNECT_HOST:-}" || -n "${OP_SERVICE_ACCOUNT_TOKEN:-}" ]]; then
  die "1Password auth env vars are set but the op CLI is not installed"
fi

# Connect takes precedence over service-account credentials in the CLI too,
# but leaving both set makes op's behaviour depend on its internal precedence.
# Drop the loser so there is exactly one auth method in play.
if [[ "$auth" == "connect" && -n "${OP_SERVICE_ACCOUNT_TOKEN:-}" ]]; then
  printf 'run-optimizer: Connect and service-account credentials both set; using Connect\n' >&2
  unset OP_SERVICE_ACCOUNT_TOKEN
fi

if [[ -n "$auth" ]]; then
  [[ -n "$SECRET_REF" ]] || die "1Password auth detected ($auth) but AA_SECRET_REF is not set (expected op://<vault>/<item>/AA_API_KEY)"
  printf 'run-optimizer: reading AA_API_KEY from 1Password (%s)\n' "$auth" >&2
  key="$(op read --no-newline "$SECRET_REF")" || die "op read failed (auth: $auth)"
  [[ -n "$key" ]] || die "op read returned an empty AA_API_KEY"
elif [[ -n "${AA_API_KEY:-}" ]]; then
  printf 'run-optimizer: no 1Password auth detected; using exported AA_API_KEY\n' >&2
  key="$AA_API_KEY"
else
  die "no 1Password auth (connect, service account or signed-in account) and AA_API_KEY is unset"
fi

export AA_API_KEY="$key"
unset key

if command -v uv >/dev/null 2>&1; then
  exec uv run opencode-go-model-optimizer select --mode "$MODE" --format table --explain "$@"
fi
exec opencode-go-model-optimizer select --mode "$MODE" --format table --explain "$@"

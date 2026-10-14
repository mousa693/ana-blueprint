#!/usr/bin/env bash
# End-to-end smoke test of a running ANA server through its public JSON API.
#
#   scripts/smoke.sh [BASE_URL]            # default http://127.0.0.1:8000
#
# Flow: healthz -> signup -> onboarding (fetch questions, answer them) ->
# create journey -> pending -> act x3 -> end -> debrief -> export ->
# delete account -> confirm the session is gone (401).
#
# Uses curl with a private cookie jar, sends the Origin header the app's CSRF
# guard expects and JSON bodies. Exits non-zero on the first failure. The
# throwaway account's password and the session cookie are never printed.
#
# Environment (all optional):
#   SMOKE_ORIGIN       Origin header to send (default: the origin of BASE_URL;
#                      set it to ANA_PUBLIC_ORIGIN when BASE_URL is not that origin)
#   SMOKE_HOST         Host header to send instead of BASE_URL's host
#   SMOKE_INVITE_CODE  invite code, when the server has ANA_INVITE_CODES set
#   SMOKE_EMAIL        account email (default: a random smoke+...@example.com)
#   SMOKE_TIMEOUT      per-request timeout in seconds (default 60)
#
# Requires: bash, curl, python3 (JSON parsing only).
set -euo pipefail

BASE="${1:-${SMOKE_BASE_URL:-http://127.0.0.1:8000}}"
BASE="${BASE%/}"
TIMEOUT="${SMOKE_TIMEOUT:-60}"

case "$BASE" in
  http://*|https://*) ;;
  *) echo "usage: $0 http(s)://host[:port]" >&2; exit 2 ;;
esac
command -v curl >/dev/null || { echo "smoke: curl is required" >&2; exit 2; }
command -v python3 >/dev/null || { echo "smoke: python3 is required" >&2; exit 2; }

ORIGIN="${SMOKE_ORIGIN:-$(python3 -c 'import sys,urllib.parse as u;p=u.urlsplit(sys.argv[1]);print(f"{p.scheme}://{p.netloc}")' "$BASE")}"

WORK="$(mktemp -d "${TMPDIR:-/tmp}/ana-smoke.XXXXXX")"
chmod 700 "$WORK"
trap 'rm -rf "$WORK"' EXIT
JAR="$WORK/cookies.txt"
BODY="$WORK/body.json"
: >"$JAR"

RAND="$(python3 -c 'import secrets;print(secrets.token_hex(6))')"
EMAIL="${SMOKE_EMAIL:-smoke+${RAND}@example.com}"
# Never echoed; kept in a file so it does not show up in `ps` either.
python3 -c 'import secrets;print(secrets.token_urlsafe(24), end="")' >"$WORK/pw"
STEP=0

fail() {
  echo "FAIL  $*" >&2
  if [ -s "$BODY" ]; then
    echo "----- response body (first 600 bytes) -----" >&2
    head -c 600 "$BODY" >&2; echo >&2
  fi
  exit 1
}

# req METHOD PATH EXPECTED_STATUS [JSON_BODY_FILE]
req() {
  local method="$1" path="$2" want="$3" data="${4:-}" code
  local -a args=(-sS -o "$BODY" -w '%{http_code}' --max-time "$TIMEOUT"
                 -X "$method" -b "$JAR" -c "$JAR"
                 -H "Accept: application/json" -H "Origin: $ORIGIN")
  [ -n "${SMOKE_HOST:-}" ] && args+=(-H "Host: $SMOKE_HOST")
  [ -n "$data" ] && args+=(-H "Content-Type: application/json" --data-binary "@$data")
  : >"$BODY"
  code="$(curl "${args[@]}" "$BASE$path")" || fail "$method $path: curl error"
  STEP=$((STEP + 1))
  if [ "$code" != "$want" ]; then
    fail "$method $path: expected HTTP $want, got $code"
  fi
  printf 'ok    %2d  %-6s %-34s %s\n' "$STEP" "$method" "$path" "$code"
}

# jget PY_EXPR — evaluate an expression over the last response body `d`.
jget() {
  python3 - "$BODY" "$1" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
v = eval(sys.argv[2], {"d": d})
print(v if isinstance(v, str) else json.dumps(v))
PY
}

# jcheck PY_EXPR MESSAGE — assert an expression over the last response body.
jcheck() {
  python3 - "$BODY" "$1" <<'PY' || fail "$2"
import json, sys
d = json.load(open(sys.argv[1]))
sys.exit(0 if eval(sys.argv[2], {"d": d}) else 1)
PY
}

echo "smoke: $BASE (Origin: $ORIGIN)"

# 1. health
req GET /healthz 200
jcheck 'd.get("ok") is True and d.get("db") is True' "/healthz is not ok"

# 2. signup (the cookie lands in the jar)
python3 - "$EMAIL" "$WORK/pw" "${SMOKE_INVITE_CODE:-}" >"$WORK/signup.json" <<'PY'
import json, sys
body = {"email": sys.argv[1], "password": open(sys.argv[2]).read(), "age_confirmed": True}
if sys.argv[3]:
    body["invite_code"] = sys.argv[3]
print(json.dumps(body))
PY
req POST /api/auth/signup 201 "$WORK/signup.json"
rm -f "$WORK/signup.json"
jcheck 'd["user"]["email"] == "'"$EMAIL"'"' "signup returned the wrong user"
grep -q "$(printf '\t')${SMOKE_COOKIE:-ana_session}$(printf '\t')" "$JAR" || fail "no session cookie was set (Secure cookie over plain http to a non-localhost host?)"

req GET /api/me 200
jcheck 'd["has_identity"] is False' "new account already has an identity"

# 3. onboarding: fetch the questions and answer every one of them
req GET /api/onboarding 200
python3 - "$BODY" >"$WORK/answers.json" <<'PY'
import json, sys
qs = json.load(open(sys.argv[1]))["questions"]
text = {"name": "Smoke", "become": "someone who can speak up",
        "place": "a lighthouse on a grey shore", "belief": "people can change"}
answers = {}
for q in qs:
    kind, opts = q.get("kind"), q.get("options") or []
    if kind == "text":
        answers[q["id"]] = text.get(q["id"], "a quiet morning")
    elif kind == "multi":
        answers[q["id"]] = [] if q["id"] == "never_show" else [o["value"] for o in opts[:2]]
    elif kind == "choice":
        answers[q["id"]] = opts[0]["value"] if opts else None
    elif kind == "scale":
        answers[q["id"]] = (q.get("min", 1) + q.get("max", 5)) // 2
if not answers:
    sys.exit("no onboarding questions")
print(json.dumps({"answers": answers}))
PY
req POST /api/onboarding 201 "$WORK/answers.json"
jcheck '"identity" in d and d["identity"].get("name")' "onboarding did not return an identity"

# 4. journey
echo '{}' >"$WORK/empty.json"
req POST /api/journeys 201 "$WORK/empty.json"
JID="$(jget 'd["journey"]["id"]')"
[ -n "$JID" ] || fail "no journey id"
jcheck 'd["pending"]["scene"]["title"]' "new journey has no pending scene"

req GET "/api/journeys/$JID/pending" 200
jcheck '"pending" in d' "journey has no pending scene"

# 5. three turns: a choice, free text, a choice
for turn in 1 2 3; do
  if [ "$turn" = 2 ]; then
    echo '{"text": "I climb the stair and light the lamp."}' >"$WORK/act.json"
  else
    echo '{"choice_index": 0}' >"$WORK/act.json"
  fi
  req POST "/api/journeys/$JID/act" 200 "$WORK/act.json"
  jcheck '"record" in d and isinstance(d["record"].get("tick"), int)' "turn $turn returned no record"
  jcheck '"safety" not in d or d["safety"].get("level") != "crisis"' "turn $turn tripped the crisis screen"
  if [ "$(jget 'd.get("closed", False)')" = "true" ]; then
    echo "note  journey came home after turn $turn"
    break
  fi
done

req GET "/api/journeys/$JID" 200
jcheck 'len(d["records"]) >= 1' "journey has no records"

# 6. end + debrief
req POST "/api/journeys/$JID/end" 200
jcheck 'd["debrief_ready"] is True' "end did not report debrief_ready"
req GET "/api/journeys/$JID/debrief" 200
jcheck 'd.get("debrief") and isinstance(d.get("markdown"), str) and d["markdown"].strip()' "empty debrief"

# 7. export
req GET /api/me/export 200
python3 - "$BODY" "$JID" <<'PY' || fail "export does not contain the journey"
import json, sys
sys.exit(0 if sys.argv[2] in open(sys.argv[1]).read() else 1)
PY

# 8. delete the account, then the session must be gone
python3 - "$WORK/pw" >"$WORK/delete.json" <<'PY'
import json, sys
print(json.dumps({"password": open(sys.argv[1]).read()}))
PY
req DELETE /api/me 204 "$WORK/delete.json"
rm -f "$WORK/delete.json"
req GET /api/me 401
jcheck 'd["error"]["code"] == "unauthenticated"' "expected an unauthenticated error"

echo "smoke: PASS ($STEP requests)"

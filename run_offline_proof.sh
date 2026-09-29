#!/bin/bash
# Offline proof run. Turn Wi-Fi/Ethernet/VPN off first, then:  ./run_offline_proof.sh
# It refuses to run if the internet is reachable, restarts Ollama and the CLI cold, ingests a new source twice,
# runs every test, and saves everything to tests/results/ (offline-proof-<time>.log, offline.json, evidence-offline.md).
cd "$(dirname "$0")" || exit 1
mkdir -p tests/results
TS=$(date +%Y%m%d-%H%M%S)
LOG="tests/results/offline-proof-$TS.log"
exec > >(tee "$LOG") 2>&1

step() { echo; echo "================ $* ================"; }
run()  { echo "\$ $*"; "$@"; echo "[exit code $?]"; }

step "0. Device and runtime"
run sw_vers; run sysctl -n machdep.cpu.brand_string; run sysctl -n hw.memsize; run ollama --version; run ollama list
date

step "1. Prove there is NO internet (these must fail)"
if curl -sS -m 6 -o /dev/null https://example.com 2>/dev/null || ping -c 1 -t 3 8.8.8.8 >/dev/null 2>&1; then
  echo "ABORT: the internet is still reachable. Turn off Wi-Fi/Ethernet/tethering/VPN and run again."
  echo "(nothing was tested; no offline claim can be made from this run)"
  exit 2
fi
echo "\$ curl -sS -m 6 https://example.com"; curl -sS -m 6 https://example.com; echo "[curl failed as expected]"
echo "\$ ping -c 2 -t 3 8.8.8.8"; ping -c 2 -t 3 8.8.8.8; echo "[ping failed as expected]"

step "2. Cold restart of Ollama; local API must still work"
pkill -f ollama; sleep 3
(nohup ollama serve > /tmp/ollama.log 2>&1 &); sleep 5
run curl -s http://127.0.0.1:11434/api/version
echo

step "3. Ingest a NEW source offline, twice (second run must write 0 files)"
run ./wiki ingest "tests/new_source/Coffee Chat Etiquette.md"
echo "notes in vault: $(find vault -name '*.md' | wc -l)"
run ./wiki ingest "tests/new_source/Coffee Chat Etiquette.md"
echo "notes in vault after re-ingest: $(find vault -name '*.md' | wc -l)  (must be unchanged)"

step "4. CLI modes"
run ./wiki help
run ./wiki search "competency areas"
run ./wiki search "coffee chat thank-you note"
run ./wiki ask "What are the four competency areas that FIT interviewers assess?"
run ./wiki ask "How soon after a coffee chat should a thank-you note be sent?"
run ./wiki ask "What was Bain & Company's global revenue in 2024?"
echo "\$ ./wiki chat   (casual, follow-up, wiki-aware, then /ask separation)"
printf '%s\n' "Hey Milo! Quick vibe check: how is your day going?" "Give me three short names for a pet goldfish." \
  "Which of those is the funniest, and why?" "Help me polish one of my stories into three sentences." \
  "Remember this: my lucky number is 47." "/ask What is my lucky number?" "/quit" | ./wiki chat
run ./wiki status

step "5. Full automated suite, evidence capture, vault checks"
run .venv/bin/python tests/run_tests.py offline
run .venv/bin/python tests/capture_evidence.py offline
run .venv/bin/python tests/check_vault.py

step "6. Confirm still offline at the end (must fail)"
curl -sS -m 6 -o /dev/null https://example.com && echo "WARNING: internet came back during the run; results are NOT valid offline evidence" || echo "still offline: OK"

echo; echo "Saved: $LOG  tests/results/offline.json  tests/results/evidence-offline.md  outputs/"
echo "Now you can reconnect. Tell Claude 'offline run done'."

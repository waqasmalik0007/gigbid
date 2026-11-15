#!/usr/bin/env bash
# Free public deployment from your own machine: GigSim + API + web + a Cloudflare quick tunnel, each
# restarted if it exits (docs/DEPLOY.md, option B). The current public URL is written to $L/URL.txt.
#   L=/path/to/live-copy PY=/path/to/python NODE=/path/to/node CLOUDFLARED=/path/to/cloudflared bash tools/serve_public.sh
L="${L:-/d/gigbid_work/live}"; cd "$L"
PY="${PY:-python}"; NODE="${NODE:-node}"; CLOUDFLARED="${CLOUDFLARED:-cloudflared}"
export PYTHONPATH="$L" GIGSIM_DB="$L/gigsim_live.sqlite3" GIGBID_APP_DB="$L/app.sqlite3" \n  PYTHONIOENCODING=utf-8 OPENBLAS_NUM_THREADS=1
mkdir -p "$L/logs"
keep() { local name=$1; shift; while true; do echo "--- $(date +%T) start $name" >> "$L/logs/$name.log"; "$@" >> "$L/logs/$name.log" 2>&1; sleep 5; done; }
keep gigsim "$PY" -m uvicorn gigsim.app:app --host 127.0.0.1 --port 8101 --log-level warning &
keep api "$PY" -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8000 --log-level warning &
(cd web && export PORT=3000 HOSTNAME=127.0.0.1 NODE_ENV=production && keep web "$NODE" server.js) &
keep tunnel "$CLOUDFLARED" tunnel --no-autoupdate --url http://127.0.0.1:3000 &
# publish the current tunnel URL
while true; do u=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' logs/tunnel.log 2>/dev/null | tail -1)
  [ -n "$u" ] && [ "$u" != "$(cat URL.txt 2>/dev/null)" ] && echo "$u" > URL.txt; sleep 10; done

#!/usr/bin/env bash
# Build a Hugging Face Docker Space from this repo (single container, docs/DEPLOY.md).
#   tools/make_space.sh <hf-username> [space-name]
set -euo pipefail
USER_HF="$1"; SPACE="${2:-gigbid}"
OUT="${SPACE_DIR:-../gigbid-space}"
rm -rf "$OUT" && mkdir -p "$OUT"
git archive HEAD | tar -x -C "$OUT"
cp infra/docker/allinone.Dockerfile "$OUT/Dockerfile"
cat > "$OUT/README.md" <<MD
---
title: GigBid
emoji: 🤝
colorFrom: indigo
colorTo: green
sdk: docker
app_port: 7860
pinned: false
---
GigBid: multi-agent freelancer copilot. Source: https://github.com/<you>/gigbid
MD
cd "$OUT"
git init -q && git add -A && git commit -qm "Deploy GigBid"
git remote add space "https://huggingface.co/spaces/$USER_HF/$SPACE"
echo "Space prepared in $OUT. Push with: cd $OUT && git push space HEAD:main --force"

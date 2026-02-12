#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

# Activate venv
source .venv/bin/activate

# DB location
export PARTYBOX_DB="${PARTYBOX_DB:-$(pwd)/data/partybox.db}"

# Run the app (binds to 0.0.0.0:5000 inside app.py)
python -m partybox.app

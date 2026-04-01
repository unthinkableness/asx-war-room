#!/bin/bash

# start_cloud.sh
# ═══════════════════════════════════════════════════════════
# Launch script for ASX Momentum Hunter Cloud Deployment
# Starts both the Dashboard API and the Autonomous Loop.
# ═══════════════════════════════════════════════════════════

echo "🚀 Starting ASX Momentum Hunter Cloud Services..."

# 1. Start the Dashboard API in the background
# Bind to 0.0.0.0 using the cloud-assigned PORT
echo "[API] Launching Dashboard API..."
python execution/dashboard_api.py &

# 2. Wait briefly for API to stabilize
sleep 2

# 3. Start the Orchestrator Loop in the foreground
# --loop: runs 24/7 during market hours
# --live: executes real trades
echo "[Loop] Launching Autonomous Orchestrator (Live)..."
python orchestrator.py --loop --live

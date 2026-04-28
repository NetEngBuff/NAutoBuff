#!/bin/bash
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "${SCRIPT_DIR}"

# 1. Load the environment (Reuse Python 3.12)
export PYENV_ROOT="$HOME/.pyenv"
export PATH="$PYENV_ROOT/bin:$PATH"
eval "$(pyenv init -)"
pyenv shell 3.12.8

# 2. Activate the existing VENV
source venv/bin/activate

echo "Building Docker images for hosts..."
sudo docker build -f Dockerfile_Hosts -t hosts:latest .

echo "Applying Netplan configurations..."
sudo cp "${SCRIPT_DIR}/netcfg.yaml" /etc/netplan/100-netcfg.yaml
sudo chmod 600 /etc/netplan/100-netcfg.yaml
sudo netplan apply

echo "Running pilot.py (Logic initialization)..."
python pilot.py

echo "Environment Setup Complete."
echo ""
echo "  Next step:  ./run_nautobuff.sh"
echo "  Access the web UI at http://$(hostname -I | awk '{print $1}'):5555"
echo ""

# ── GitHub Webhook Instructions ──────────────────────────────────────────────
NGROK_LOG=$(find "${SCRIPT_DIR}/../NSOT/logs" -name "ngrok.log" 2>/dev/null | head -1)
NGROK_URL=""
if [ -n "$NGROK_LOG" ]; then
    NGROK_URL=$(grep -oP 'url=https://[a-zA-Z0-9\-]+\.ngrok-free\.app' "$NGROK_LOG" 2>/dev/null | tail -1 | cut -d= -f2)
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  GitHub Webhook Setup (one-time manual step)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if [ -n "$NGROK_URL" ]; then
    echo "  Webhook URL: ${NGROK_URL}/github-webhook/"
else
    echo "  Webhook URL: <ngrok not ready yet — check burger menu → External Links → Jenkins>"
fi
echo ""
echo "  Steps:"
echo "  1. Go to your GitHub repo → Settings → Webhooks → Add webhook"
if [ -n "$NGROK_URL" ]; then
    echo "  2. Payload URL: ${NGROK_URL}/github-webhook/"
else
    echo "  2. Payload URL: <ngrok URL>/github-webhook/"
fi
echo "  3. Content type: application/json"
echo "  4. Click 'Add webhook'"
echo ""
echo "  Note: Ngrok URL changes on restart — update webhook when it does."
echo "        Current URL always shown in burger menu → External Links → Jenkins"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

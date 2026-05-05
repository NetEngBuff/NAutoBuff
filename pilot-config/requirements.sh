#!/bin/bash

# Exit on any error
set -e

# CLEANUP: Remove known broken or conflicting local repo files
echo "Cleaning up existing repository conflicts..."
sudo rm -f /etc/apt/sources.list.d/*.list
sudo rm -f /etc/apt/sources.list.d/*.sources

echo "[1/12] Removing old Docker components..."
for pkg in docker.io docker-doc docker-compose docker-compose-v2 podman-docker containerd runc; do
    sudo apt-get remove -y $pkg || true
done

echo "[2/12] Setting up Docker repository..."
sudo apt-get update || true
sudo apt-get install -y ca-certificates curl gnupg software-properties-common
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor --yes -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

echo "[3/12] Installing SSH Server and Containerlab..."
sudo apt-get update
sudo apt-get install -y openssh-server
curl -sL https://containerlab.dev/setup | sudo bash -s "all"

echo "[4/12] Installing InfluxDB 2.x..."
curl --silent --location -O https://repos.influxdata.com/influxdata-archive.key
cat influxdata-archive.key | gpg --dearmor --yes | sudo tee /etc/apt/keyrings/influxdata-archive.gpg > /dev/null
echo 'deb [signed-by=/etc/apt/keyrings/influxdata-archive.gpg] https://repos.influxdata.com/debian stable main' | sudo tee /etc/apt/sources.list.d/influxdata.list
rm -f influxdata-archive.key
sudo apt-get update && sudo apt-get install -y influxdb2
sudo systemctl enable --now influxdb

echo "[5/12] Installing Grafana..."
curl -fsSL https://apt.grafana.com/gpg.key | gpg --dearmor --yes | sudo tee /etc/apt/keyrings/grafana.gpg > /dev/null
echo "deb [signed-by=/etc/apt/keyrings/grafana.gpg] https://apt.grafana.com stable main" | sudo tee /etc/apt/sources.list.d/grafana.list
sudo apt-get update && sudo apt-get install -y grafana

# Allow Grafana to be embedded in iframes (required for the NAutoBuff dashboard page)
sudo sed -i 's/;allow_embedding = .*/allow_embedding = true/' /etc/grafana/grafana.ini || true
# Enable anonymous viewer access so the iframe loads without requiring a Grafana login
sudo sed -i '/^\[auth\.anonymous\]/,/^\[/ s/;enabled = false/enabled = true/' /etc/grafana/grafana.ini || true
sudo sed -i '/^\[auth\.anonymous\]/,/^\[/ s/;org_name = .*/org_name = Main Org./' /etc/grafana/grafana.ini || true
sudo sed -i '/^\[auth\.anonymous\]/,/^\[/ s/;org_role = .*/org_role = Viewer/' /etc/grafana/grafana.ini || true

sudo systemctl enable --now grafana-server

echo "[6/12] Installing Ngrok..."
curl -sSL https://ngrok-agent.s3.amazonaws.com/ngrok.asc | sudo tee /etc/apt/trusted.gpg.d/ngrok.asc > /dev/null
echo "deb https://ngrok-agent.s3.amazonaws.com bookworm main" | sudo tee /etc/apt/sources.list.d/ngrok.list
sudo apt-get update && sudo apt-get install -y ngrok

echo "[7/12] Installing Java (OpenJDK 21)..."
sudo apt-get install -y fontconfig openjdk-21-jre

echo "[8/12] Installing Jenkins..."
sudo wget -O /etc/apt/keyrings/jenkins-keyring.asc https://pkg.jenkins.io/debian-stable/jenkins.io-2026.key
echo "deb [signed-by=/etc/apt/keyrings/jenkins-keyring.asc] https://pkg.jenkins.io/debian-stable binary/" | sudo tee /etc/apt/sources.list.d/jenkins.list > /dev/null
sudo apt-get update && sudo apt-get install -y jenkins
echo "✅ Jenkins installed — first-time configuration handled by pilot.py"

echo "[9/12] Setting up Python 3.12 via pyenv..."
export PYENV_ROOT="$HOME/.pyenv"
export PATH="$PYENV_ROOT/bin:$PATH"
eval "$(pyenv init -)"

# Ensure we have the build tools for ARM64 Python compilation
sudo apt install -y build-essential libssl-dev zlib1g-dev libbz2-dev libreadline-dev libsqlite3-dev libncurses-dev xz-utils tk-dev libxml2-dev libxmlsec1-dev libffi-dev liblzma-dev

if ! pyenv versions | grep -q "3.12.8"; then
    pyenv install 3.12.8
fi
pyenv shell 3.12.8

rm -rf venv
python -m venv venv

echo "[10/12] Installing Network & Automation tools..."
sudo apt-get install -y libsnmp-dev snmp snmpd snmptrapd snmp-mibs-downloader gcc syslog-ng telegraf git-lfs gnmic xdg-utils graphviz socat netplan.io net-tools
sudo add-apt-repository universe -y
sudo download-mibs || true

echo "[11/12] Installing Python packages..."
./venv/bin/pip install --upgrade pip setuptools wheel
# Fix for GCC 15 strictness on Questing
CFLAGS="-Wno-error=incompatible-pointer-types" ./venv/bin/pip install easysnmp
./venv/bin/pip install -r requirements.txt

echo "[12/13] Finalizing Permissions..."
sudo usermod -aG docker $USER || true
sudo usermod -aG docker jenkins || true
# The "Magic" fix for sudo-less Containerlab in the Web UI
sudo setcap cap_net_admin,cap_net_raw,cap_sys_admin+ep $(which containerlab)
sudo chmod o+rx $HOME

echo "[13/14] MCP HTTP Servers — Optional..."
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  MCP servers let Claude Desktop (or other remote AI"
echo "  clients) reach NAutoBuff devices over the network."
echo ""
echo "  This opens two ports on this machine:"
echo "    - 8001  MCP Query  (show commands)"
echo "    - 8002  MCP Config (push config, rollback, backup)"
echo ""
echo "  Enable this if you want to use it with Claude Desktop,"
echo "  ChatGPT, or any AI client running on a remote machine."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
read -rp "  Enable MCP HTTP servers? [y/N]: " ENABLE_MCP_HTTP
SCRIPT_DIR_MCP="$(cd "$(dirname "$0")" && pwd)"
if [[ "${ENABLE_MCP_HTTP,,}" == "y" ]]; then
    touch "${SCRIPT_DIR_MCP}/../NSOT/misc/.mcp_http_enabled"
    echo "✅ MCP HTTP servers will be started by pilot.sh"
else
    rm -f "${SCRIPT_DIR_MCP}/../NSOT/misc/.mcp_http_enabled"
    echo "⚠️  MCP HTTP servers skipped — Claude Code over SSH still works via .mcp.json"
fi

echo "[14/14] AI Chatbot (NBot) — Optional..."
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  NBot is an AI assistant that lets you query your"
echo "  network in plain English (e.g. 'show BGP neighbors"
echo "  on R1', 'get interface errors on Ethernet1')."
echo ""
echo "  This will install:"
echo "    - Ollama  (local AI runtime, ~50MB binary)"
echo "    - llama3.1:8b model  (~5GB download)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
read -rp "  Enable AI chatbot? [y/N]: " ENABLE_CHATBOT
SCRIPT_DIR_OLLAMA="$(cd "$(dirname "$0")" && pwd)"
if [[ "${ENABLE_CHATBOT,,}" == "y" ]]; then
    touch "${SCRIPT_DIR_OLLAMA}/../NSOT/misc/.ollama_enabled"
    echo "  Installing Ollama runtime..."
    # zstd is required by the Ollama installer — install from source if not in apt
    if ! command -v zstd &>/dev/null; then
        echo "  Installing zstd (required by Ollama installer)..."
        TMP_ZSTD=$(mktemp -d)
        curl -fsSL https://github.com/facebook/zstd/releases/download/v1.5.6/zstd-1.5.6.tar.gz | tar -xz -C "${TMP_ZSTD}" --strip-components=1
        make -C "${TMP_ZSTD}" -j$(nproc) -s && sudo make -C "${TMP_ZSTD}" install -s
        rm -rf "${TMP_ZSTD}"
    fi
    curl -fsSL https://ollama.com/install.sh | sh
    echo "  Pulling llama3.1:8b model (~5GB — this may take a few minutes)..."
    ollama pull llama3.1:8b
    echo "✅ AI chatbot enabled — NBot will be available in the web UI"
else
    rm -f "${SCRIPT_DIR_OLLAMA}/../NSOT/misc/.ollama_enabled"
    echo "⚠️  AI chatbot skipped."
    echo "   To enable later:"
    echo "     curl -fsSL https://ollama.com/install.sh | sh"
    echo "     ollama pull llama3.1:8b"
fi

# ── Ngrok configuration ──────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
NGROK_CONFIG="${SCRIPT_DIR}/../NSOT/misc/ngrok_config.yml"
mkdir -p "$(dirname "${NGROK_CONFIG}")"

if [ -f "${NGROK_CONFIG}" ]; then
    echo "✅ Ngrok config already exists — skipping"
else
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  Ngrok tunnels your local Jenkins to GitHub for CI/CD"
    echo "  Get your free auth token: https://dashboard.ngrok.com"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    read -rp "  Enter your Ngrok auth token (Enter to skip): " NGROK_TOKEN
    if [ -n "${NGROK_TOKEN}" ]; then
        cat > "${NGROK_CONFIG}" <<EOF
---
version: "2"
authtoken: ${NGROK_TOKEN}
region: us
tunnels:
  jenkins:
    addr: 8080
    proto: http
EOF
        echo "✅ Ngrok config written to ${NGROK_CONFIG}"
    else
        echo "⚠️  Skipped — create ${NGROK_CONFIG} manually before running pilot.py"
        echo "   Format: authtoken: <your_token>  tunnels.jenkins.addr: 8080"
    fi
fi

# ── MCP health check — write .mcp.json if both server files are present ──────
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MCP_JSON="${REPO_ROOT}/.mcp.json"
DUT_QUERY="${REPO_ROOT}/NSOT/mcp/dut_query.py"
DUT_CONFIG="${REPO_ROOT}/NSOT/mcp/dut_config.py"

if [[ -f "${DUT_QUERY}" && -f "${DUT_CONFIG}" ]]; then
    cat > "${MCP_JSON}" <<'EOF'
{
  "mcpServers": {
    "nautobuff-query": {
      "command": "pilot-config/venv/bin/python",
      "args": ["NSOT/mcp/dut_query.py"],
      "env": {
        "PYTHONPATH": "NSOT/mcp"
      }
    },
    "nautobuff-config": {
      "command": "pilot-config/venv/bin/python",
      "args": ["NSOT/mcp/dut_config.py"],
      "env": {
        "PYTHONPATH": "NSOT/mcp"
      }
    }
  }
}
EOF
    echo "✅ .mcp.json written — nautobuff-query and nautobuff-config registered"
else
    echo "⚠️  MCP server files not found — .mcp.json not updated"
    [[ ! -f "${DUT_QUERY}" ]] && echo "   Missing: NSOT/mcp/dut_query.py"
    [[ ! -f "${DUT_CONFIG}" ]] && echo "   Missing: NSOT/mcp/dut_config.py"
fi

echo ""
echo "✅ Setup Complete. Please log out and back in for group changes."
echo ""
echo "  Next step:  ./pilot.sh"

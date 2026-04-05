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

# ── Automate Jenkins first-time configuration (no wizard, no manual steps) ──

# Stop so we can configure before Jenkins writes anything
sudo systemctl stop jenkins || true

# 1. Skip the setup wizard by writing the installed version into both state files
JENKINS_VER=$(dpkg -l jenkins 2>/dev/null | awk '/^ii/{print $3}' | head -1)
for f in /var/lib/jenkins/jenkins.install.UpgradeWizard.state \
          /var/lib/jenkins/jenkins.install.InstallUtil.lastExecVersion; do
    sudo bash -c "echo '${JENKINS_VER}' > ${f}"
    sudo chown jenkins:jenkins "${f}"
done

# 2. Groovy init script — creates admin/admin and marks setup complete
sudo mkdir -p /var/lib/jenkins/init.groovy.d
sudo tee /var/lib/jenkins/init.groovy.d/01-security.groovy > /dev/null <<'GROOVY'
import jenkins.model.*
import hudson.security.*
import jenkins.install.InstallState

def instance = Jenkins.get()
if (instance.getSecurityRealm() instanceof HudsonPrivateSecurityRealm &&
    instance.getSecurityRealm().getAllUsers().find { it.id == "admin" }) {
    println("ℹ️  Jenkins admin already configured — skipping")
    return
}
def realm = new HudsonPrivateSecurityRealm(false)
realm.createAccount("admin", "admin")
instance.setSecurityRealm(realm)
def strategy = new FullControlOnceLoggedInAuthorizationStrategy()
strategy.setAllowAnonymousRead(false)
instance.setAuthorizationStrategy(strategy)
instance.setInstallState(InstallState.INITIAL_SETUP_COMPLETED)
instance.save()
println("✅ Jenkins admin user and security configured")
GROOVY
sudo chown -R jenkins:jenkins /var/lib/jenkins/init.groovy.d/

# 3. Install required plugins BEFORE first start using the Plugin Installation Manager
echo "  Installing Jenkins plugins (git, pipeline, github)..."
PLUGIN_JAR="/tmp/jenkins-plugin-manager.jar"
if [ ! -f "${PLUGIN_JAR}" ]; then
    curl -fsSL -o "${PLUGIN_JAR}" \
      "https://github.com/jenkinsci/plugin-installation-manager-tool/releases/download/2.12.15/jenkins-plugin-manager-2.12.15.jar" \
      || echo "  ⚠️  Could not download plugin manager — will install via REST API on first run"
fi
if [ -f "${PLUGIN_JAR}" ]; then
    sudo java -jar "${PLUGIN_JAR}" \
        --war /usr/share/jenkins/jenkins.war \
        --plugin-download-directory /var/lib/jenkins/plugins \
        --plugins "git:latest workflow-aggregator:latest github:latest pipeline-stage-view:latest" \
        2>&1 | tail -5 \
      && echo "  ✅ Plugins installed" \
      || echo "  ⚠️  Plugin install had warnings — pilot.py will retry via API"
    sudo chown -R jenkins:jenkins /var/lib/jenkins/plugins/ || true
fi

sudo systemctl enable --now jenkins
echo "✅ Jenkins ready (credentials: admin / admin)"

# Auto-generate Jenkins API token and save to .jenkins_creds
CREDS_FILE="$(dirname "$0")/.jenkins_creds"
if [ -f "$CREDS_FILE" ]; then
    echo "  ℹ️  $CREDS_FILE already exists — skipping token generation"
else
    echo "  Waiting for Jenkins to be ready..."
    for i in $(seq 1 24); do
        if curl -sf -u admin:admin http://localhost:8080/api/json > /dev/null 2>&1; then
            break
        fi
        sleep 5
    done

    CRUMB=$(curl -sf -u admin:admin \
        "http://localhost:8080/crumbIssuer/api/json" | \
        python3 -c "import sys,json; d=json.load(sys.stdin); print(d['crumbRequestField']+':'+d['crumb'])" 2>/dev/null)

    TOKEN_JSON=$(curl -sf -u admin:admin \
        -H "$CRUMB" \
        -X POST \
        "http://localhost:8080/user/admin/descriptorByName/jenkins.security.ApiTokenProperty/generateNewToken" \
        --data "newTokenName=nautobuff" 2>/dev/null)

    TOKEN=$(echo "$TOKEN_JSON" | python3 -c \
        "import sys,json; d=json.load(sys.stdin); print(d['data']['tokenValue'])" 2>/dev/null)

    if [ -n "$TOKEN" ]; then
        cat > "$CREDS_FILE" <<EOF
export JENKINS_USER=admin
export JENKINS_TOKEN=$TOKEN
export JENKINS_JOB_NAME=NAutoBuff
EOF
        chmod 600 "$CREDS_FILE"
        echo "✅ Jenkins API token saved to pilot-config/.jenkins_creds"
    else
        echo "⚠️  Could not auto-generate Jenkins token."
        echo "   After setup, create pilot-config/.jenkins_creds manually:"
        echo "     export JENKINS_USER=admin"
        echo "     export JENKINS_TOKEN=<Jenkins → username → Configure → API Token>"
        echo "     export JENKINS_JOB_NAME=NAutoBuff"
    fi
fi

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
if [[ "${ENABLE_CHATBOT,,}" == "y" ]]; then
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

echo ""
echo "✅ Setup Complete. Please log out and back in for group changes."
echo ""
echo "  Next step:  cd pilot-config && python pilot.py"
echo ""
echo "  One manual step remains after pilot.py:"
echo "  → Add GitHub webhook: repo Settings → Webhooks → <ngrok_url>/github-webhook/"
echo "  → The current ngrok URL is shown in the NAutoBuff burger menu → External Links → Jenkins"

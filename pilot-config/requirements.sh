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

# Allow Grafana to be embedded in iframes (required for the NAutoHUB dashboard page)
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

echo "[12/12] Finalizing Permissions..."
sudo usermod -aG docker $USER || true
sudo usermod -aG docker jenkins || true
# The "Magic" fix for sudo-less Containerlab in the Web UI
sudo setcap cap_net_admin,cap_net_raw,cap_sys_admin+ep $(which containerlab)
sudo chmod o+rx $HOME

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
echo "  → The current ngrok URL is shown in the NAutoHUB burger menu → External Links → Jenkins"

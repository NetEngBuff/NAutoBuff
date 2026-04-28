#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

ASSUME_YES=0
PURGE_PACKAGES=0
PURGE_DATA=0

usage() {
  cat <<'EOF'
NAutoBuff kill switch

Usage:
  ./kill_switch.sh [--yes] [--purge-packages] [--purge-data]

Default cleanup:
  - stops/disables/removes NAutoBuff systemd services
  - destroys Containerlab labs from pilot-config/topo.yml
  - removes generated NAutoBuff runtime files, venv, MCP flags, ngrok config
  - removes NAutoBuff netplan file /etc/netplan/100-netcfg.yaml
  - removes the hosts:latest Docker image built by pilot.sh

Optional:
  --purge-packages  also purge packages/repos installed by requirements.sh
  --purge-data      also purge Jenkins/Grafana/InfluxDB data directories
  --yes             do not prompt for confirmation

Examples:
  ./kill_switch.sh
  ./kill_switch.sh --purge-packages --purge-data
  ./kill_switch.sh --yes --purge-packages --purge-data
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes|-y) ASSUME_YES=1 ;;
    --purge-packages) PURGE_PACKAGES=1 ;;
    --purge-data) PURGE_DATA=1 ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
  shift
done

confirm() {
  if [[ "${ASSUME_YES}" == "1" ]]; then
    return
  fi
  echo "This will stop NAutoBuff services and remove generated runtime state."
  if [[ "${PURGE_PACKAGES}" == "1" ]]; then
    echo "It will also purge third-party packages installed by requirements.sh."
  fi
  if [[ "${PURGE_DATA}" == "1" ]]; then
    echo "It will also remove Jenkins, Grafana, and InfluxDB data directories."
  fi
  read -rp "Type RESET to continue: " answer
  [[ "${answer}" == "RESET" ]] || { echo "Aborted."; exit 1; }
}

run() {
  echo "+ $*"
  "$@" || true
}

sudo_run() {
  echo "+ sudo $*"
  sudo "$@" || true
}

confirm

echo "[1/8] Stopping NAutoBuff-managed services..."
SERVICES=(
  password_update.service
  device_health_check.timer
  device_health_check.service
  ipam.service
  gnmic_nautobuff.service
  ngrok.service
  nautobuff_mcp_query.service
  nautobuff_mcp_config.service
)

for unit in "${SERVICES[@]}"; do
  sudo_run systemctl stop "${unit}"
  sudo_run systemctl disable "${unit}"
  sudo_run rm -f "/etc/systemd/system/${unit}"
done
sudo_run systemctl daemon-reload
sudo_run systemctl reset-failed

echo "[2/8] Destroying Containerlab topology and graph process..."
sudo_run pkill -f "containerlab graph"
if command -v containerlab >/dev/null 2>&1 && [[ -f "${REPO_ROOT}/pilot-config/topo.yml" ]]; then
  sudo_run containerlab destroy -t "${REPO_ROOT}/pilot-config/topo.yml" --cleanup
fi
find "${REPO_ROOT}/pilot-config" -maxdepth 1 -type d -name 'clab-*' -print -exec rm -rf {} +

echo "[3/8] Removing generated NAutoBuff runtime files..."
rm -rf "${REPO_ROOT}/pilot-config/venv"
rm -f "${REPO_ROOT}/pilot-config/.jenkins_creds"
rm -f "${REPO_ROOT}/.mcp.json"
rm -f "${REPO_ROOT}/gnmic-stream.yaml"
rm -f "${REPO_ROOT}/NSOT/misc/.mcp_http_enabled"
rm -f "${REPO_ROOT}/NSOT/misc/ngrok_config.yml"
rm -f "${REPO_ROOT}/NSOT/logs/ngrok.log"
rm -f "${REPO_ROOT}/NSOT/templates/devices_config.yml"
rm -f "${REPO_ROOT}/NSOT/IPAM/ipam_output.csv"
find "${REPO_ROOT}/NSOT/configs" -type f -name '*.cfg' -delete 2>/dev/null || true
find "${REPO_ROOT}/NSOT/golden_configs" -type f -name '*.cfg' -delete 2>/dev/null || true

echo "[4/8] Removing NAutoBuff netplan overlay..."
sudo_run rm -f /etc/netplan/100-netcfg.yaml
if command -v netplan >/dev/null 2>&1; then
  sudo_run netplan apply
fi

echo "[5/8] Removing Docker artifacts created by pilot.sh..."
if command -v docker >/dev/null 2>&1; then
  run docker image rm -f hosts:latest
fi

echo "[6/8] Removing apt repo files/keyrings created by requirements.sh..."
sudo_run rm -f /etc/apt/sources.list.d/docker.list
sudo_run rm -f /etc/apt/sources.list.d/influxdata.list
sudo_run rm -f /etc/apt/sources.list.d/grafana.list
sudo_run rm -f /etc/apt/sources.list.d/ngrok.list
sudo_run rm -f /etc/apt/sources.list.d/jenkins.list
sudo_run rm -f /etc/apt/keyrings/docker.gpg
sudo_run rm -f /etc/apt/keyrings/influxdata-archive.gpg
sudo_run rm -f /etc/apt/keyrings/grafana.gpg
sudo_run rm -f /etc/apt/keyrings/jenkins-keyring.asc
sudo_run rm -f /etc/apt/trusted.gpg.d/ngrok.asc

if [[ "${PURGE_PACKAGES}" == "1" ]]; then
  echo "[7/8] Purging packages installed by requirements.sh..."
  sudo_run systemctl stop jenkins
  sudo_run systemctl stop grafana-server
  sudo_run systemctl stop influxdb
  sudo_run systemctl stop ngrok
  sudo_run systemctl stop ollama
  sudo_run apt-get purge -y \
    containerlab \
    docker-ce docker-ce-cli docker-buildx-plugin docker-compose-plugin containerd.io \
    docker.io docker-compose docker-compose-v2 podman-docker containerd runc \
    influxdb2 grafana ngrok jenkins openjdk-21-jre \
    snmp snmpd snmptrapd snmp-mibs-downloader syslog-ng telegraf gnmic \
    graphviz socat netplan.io net-tools git-lfs xdg-utils
  sudo_run apt-get autoremove -y
  sudo_run apt-get update
else
  echo "[7/8] Package purge skipped. Re-run with --purge-packages for full package cleanup."
fi

if [[ "${PURGE_DATA}" == "1" ]]; then
  echo "[8/8] Removing service data directories..."
  sudo_run rm -rf /var/lib/jenkins /var/cache/jenkins /var/log/jenkins
  sudo_run rm -rf /var/lib/grafana /var/log/grafana /etc/grafana
  sudo_run rm -rf /var/lib/influxdb /var/lib/influxdb2 /etc/influxdb /etc/influxdb2
  rm -rf "${HOME}/.influxdbv2"
  sudo_run rm -rf /usr/share/ollama /var/lib/ollama
else
  echo "[8/8] Data purge skipped. Re-run with --purge-data to remove Jenkins/Grafana/InfluxDB/Ollama data."
fi

echo ""
echo "NAutoBuff cleanup complete."
echo "For a full fresh install, run:"
echo "  cd ${REPO_ROOT}/pilot-config"
echo "  ./requirements.sh"
echo "  ./pilot.sh"

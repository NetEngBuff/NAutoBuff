# NAutoBuff — Network Management & Automation System

NAutoBuff is a **Network Management and Automation System (NMAS)** that acts as a centralized **Network Source of Truth (NSoT)**. It combines a Flask web UI, Containerlab virtual topology management, AI-assisted network operations, gNMI/SNMP telemetry, and a Jenkins CI/CD pipeline into a single open-source platform.

![Architecture](pictures/nhub-arch.jpeg)

---

## What is a Network Source of Truth?

A Network Source of Truth (NSoT) is a single, reliable reference for all network data — configurations, device states, IP addresses, and inventory. Instead of managing devices individually, everything flows through one place.

NAutoBuff's NSoT lives in the `NSOT/` directory:

| Directory | Purpose |
|---|---|
| `GUI/` | Flask web interface (port 5555) |
| `python-files/` | Config generation, deployment, health checks, SNMP/gNMI |
| `golden_configs/` | Pre-validated configuration snapshots for rollback |
| `IPAM/` | IP Address Management and device inventory (`hosts.csv`) |
| `templates/` | Jinja2 templates for building device configs |
| `machine_learning/` | AI pipeline — intent extraction, CLI interpretation, config generation |
| `mcp/` | MCP servers for Claude/ChatGPT Desktop integration |
| `logs/` | Application log (`nautobuff.log`) and ngrok tunnel log |

---

## Features

| Feature | Description |
|---|---|
| **Topology Builder** | Deploy/destroy Containerlab topologies from the UI |
| **Config Generator** | Build device configs from Jinja2 templates (interfaces, subinterfaces, static routes, BGP, OSPF, RIP, VLANs) |
| **Config Push** | Push configs to devices via SSH (Netmiko) with CI/CD validation |
| **Config Rollback** | Atomic configure-session replace to golden config — credentials auto-synced from inventory |
| **Console Access** | Interactive browser-based SSH terminal for each device (xterm.js + WebSocket) |
| **IPAM** | Live SNMP-polled IP address table using the management IPs in `hosts.csv` |
| **Device Health** | CPU, interface, route, and LLDP neighbor health checks |
| **Telemetry** | gNMI streaming via gnmic → InfluxDB → Grafana dashboard |
| **AI Assistant (NBot)** | Natural language queries AND configuration via Ollama LLM — full CI/CD pipeline triggered on configure |
| **MCP Servers** | Claude/ChatGPT Desktop can query and configure devices via MCP tools |
| **Service Health Check** | Start/stop/restart project services from the burger menu |
| **Kill Switch** | Reset generated NAutoBuff services, lab state, runtime files, and optional installed packages |
| **External Links** | Grafana, InfluxDB, and Jenkins URLs always accessible from the nav menu |
| **Password Rotation** | Automatic credential rotation on all devices every 30 minutes |
| **App Logging** | Timestamped log at `NSOT/logs/nautobuff.log` |

---

## CI/CD Pipeline

Every config change pushed through the web UI or AI chatbot goes through a full CI/CD pipeline:

![cicd](pictures/nautobuff-cicd.png)

1. Config rendered from a Jinja2 template
2. Committed to Git and pushed to GitHub automatically
3. Jenkins picks it up via GitHub webhook
4. Pipeline runs: flake8 lint → YAML lint → unit tests
5. On success, config deployed to the device via Netmiko SSH

---

## Setup

### Prerequisites

- **Linux** — Ubuntu 22.04+ (recommended)
- **macOS** — [OrbStack](https://orbstack.dev) (lightweight Linux VM with full systemd + Docker)
- **Windows** — WSL2 with Ubuntu
- Internet connection
- A free [Ngrok account](https://dashboard.ngrok.com) — needed during `requirements.sh` for CI/CD webhook tunneling

> Containerlab requires a Linux kernel. OrbStack and WSL2 both provide this transparently.

**Before you start — get your Ngrok auth token:**
1. Sign up at [dashboard.ngrok.com](https://dashboard.ngrok.com)
2. Go to **Your Authtoken** in the left sidebar
3. Copy the token — `requirements.sh` will prompt you for it and write it to `NSOT/misc/ngrok_config.yml` automatically

---

### Step 1 — Fork and clone

> **Fork first.** The CI/CD pipeline watches *your* repo — Jenkins must point to your fork, not the original.

1. Click **Fork** at the top-right of this page
2. Make sure you have an SSH key added to your GitHub account — [GitHub SSH setup guide](https://docs.github.com/en/authentication/connecting-to-github-with-ssh)
3. Clone your fork:

```bash
mkdir -p ~/projects && cd ~/projects
git clone git@github.com:<your-username>/NAutoBuff.git
cd NAutoBuff/pilot-config
chmod +x requirements.sh pilot.sh run_nautobuff.sh kill_switch.sh
```

---

### Step 2 — Install system dependencies

```bash
./requirements.sh
```

This installs everything the system needs (run once):
- Docker, Containerlab, InfluxDB, Grafana, Ngrok, Java, Jenkins
- Python 3.12 via pyenv + virtual environment + all Python packages
- Prompts you for your **Ngrok auth token** (see Prerequisites above)
- Optional: **AI chatbot** (Ollama + llama3.1:8b, ~5GB download)
- Optional: **MCP HTTP servers** for Claude/ChatGPT Desktop integration

> **After it finishes — log out and back in** (or open a new terminal) so Docker group changes take effect.

### Clean slate / reinstall

If you want to remove runtime state created by `requirements.sh` and `pilot.sh`, run the kill switch from the repo you want to reset:

```bash
cd ~/projects/NAutoBuff/pilot-config
./kill_switch.sh
```

For a full reset that also removes third-party packages and service data:

```bash
./kill_switch.sh --purge-packages --purge-data
```

The script prompts you to type `RESET` before it changes the machine. After it completes, run `./requirements.sh` and `./pilot.sh` again.

---

### Step 3 — Download and import your network OS image

Vendor images are not included in this repo. Download and import your image manually before running `pilot.sh`. Refer to the [containerlab supported kinds documentation](https://containerlab.dev/manual/kinds/) for download and import instructions for each vendor.

---

### Step 4 — Bring up the environment

```bash
./pilot.sh
```

This configures and starts everything:
- Builds Docker images for host containers
- Applies Netplan network config
- Runs `pilot.py` which:
  - Configures Jenkins (first-time setup, creates pipeline job pointing at your repo)
  - Generates an InfluxDB API token and writes `gnmic-stream.yaml`
  - Creates Grafana datasource and telemetry dashboard
  - Creates all systemd services (IPAM, gNMI, ngrok, health check, password rotation)

At the end, webhook setup instructions are printed with your current Ngrok URL.

---

### Step 5 — Start the web UI

```bash
./run_nautobuff.sh
```

Access at: `http://<your-ip>:5555`

---

### Step 6 — Add the GitHub webhook (one-time)

This is the only manual step. Jenkins needs GitHub to notify it when you push.

1. Go to your GitHub repo → **Settings → Webhooks → Add webhook**
2. **Payload URL:** `https://<ngrok-url>/github-webhook/`
   - Your current ngrok URL is printed at the end of `./pilot.sh`
   - It's also always visible in the burger menu → **External Links → Jenkins**
3. **Content type:** `application/json`
4. Click **Add webhook**

> The Ngrok URL changes on free tier when the service restarts. Update the webhook when that happens.

---

### Step 7 — Deploy a topology and start streaming telemetry

1. Open the web UI → **Topology Builder** → deploy your lab
2. Confirm device management IPs are present in **Hosts Inventory**. For virtual labs, use the OOB subnet described on the topology page.
3. Open **IPAM**. The poller reads `hosts.csv` and discovers interface IPs over SNMP.
4. Telemetry starts flowing automatically: devices → gnmic → InfluxDB → Grafana

---

## Default Credentials

| Service | URL | Username | Password |
|---|---|---|---|
| NAutoBuff Web UI | `http://<host>:5555` | `admin` | `admin` |
| Grafana | `http://<host>:3000` | `admin` | `admin` |
| InfluxDB | `http://<host>:8086` | `admin` | `admin123` |
| Jenkins | Ngrok URL (burger menu) | `admin` | `admin` |
| cEOS devices | — | `admin` | `admin` |

> Change these after setup, especially on any internet-exposed machine.

---

## Systemd Services

Managed from **burger menu → Health Check** or via the command line:

| Service | Purpose |
|---|---|
| `ipam.service` | SNMP polling every 10s → `IPAM/ipam_output.csv` |
| `gnmic_nautobuff.service` | gNMI telemetry → InfluxDB |
| `device_health_check.timer` | Periodic CPU/interface/route checks |
| `password_update.service` | Credential rotation every 30 min |
| `ngrok.service` | Jenkins webhook tunnel |
| `ollama.service` | Local LLM runtime for NBot (if installed) |
| `jenkins` | CI/CD pipeline server |
| `influxdb` | Time-series telemetry database |
| `grafana-server` | Telemetry dashboards |

---

## Monitoring

Live telemetry log:
```bash
tail -f ~/projects/NAutoBuff/NSOT/logs/nautobuff.log
```

Check a specific service:
```bash
sudo journalctl -u gnmic_nautobuff.service -f
sudo journalctl -u ngrok.service -f
sudo journalctl -u jenkins -f
```

---

## Troubleshooting

**Docker permission denied after requirements.sh**
```bash
# Log out and back in, or run:
newgrp docker
```

**IPAM shows no device IPs**
- Confirm the topology is deployed and the devices are reachable on the management IPs in `NSOT/IPAM/hosts.csv`
- In virtual labs, the host OOB interface should be `eth1.100` with `10.0.101.200/24`
- Click **Restart IPAM Poller** on the IPAM page, then wait one polling interval

**Telemetry not showing in Grafana**
```bash
# Check gnmic is streaming
sudo journalctl -u gnmic_nautobuff.service -n 30
# Re-sync telemetry targets after deploying topology
cd ~/projects/NAutoBuff && python3 NSOT/python-files/gnmi_hosts.py
sudo systemctl restart gnmic_nautobuff.service
```

**Jenkins job not triggering on push**
- Check the GitHub webhook is set and the ngrok URL is current
- Ngrok URL is always shown in burger menu → External Links → Jenkins
```bash
sudo systemctl restart ngrok.service
```

**Jenkins API token invalid**
```bash
# Edit pilot-config/.jenkins_creds with a new token from:
# Jenkins → top-right username → Configure → API Token → Add new token
```

**Re-run Jenkins job provisioning manually**
```bash
cd ~/projects/NAutoBuff/pilot-config
python3 -c "from pilot import provision_jenkins_job; provision_jenkins_job()"
```

**Config push fails — wrong management IP**
- Check **Hosts Inventory**. Device management IPs should be user-entered lab management addresses, not Containerlab runtime `172.20.20.x` addresses.
- For virtual labs, use the `10.0.101.0/24` OOB network and verify the host has `eth1.100`.

**Rollback loses management connectivity**
- Golden config didn't include `Management0` when saved
- Fix: while devices are reachable, re-run **Tools → Golden Config Generator → All devices**

**Rollback fails after topology redeploy**
- Confirm `hosts.csv` still has the intended management IPs and credentials, then retry after the topology is reachable.

**Windows/WSL: user not in sudoers**
```powershell
# In PowerShell as Administrator:
wsl -u root
usermod -aG sudo <your_wsl_username>
```

**Reset InfluxDB (if credentials or bucket are wrong)**
```bash
sudo systemctl stop influxdb
sudo rm -rf /var/lib/influxdb/
rm -f ~/.influxdbv2/configs
sudo systemctl start influxdb
./pilot.sh
```

import csv
import json
import re
import subprocess
import urllib.request
import urllib.error
import base64
import yaml
import os

GRAFANA_URL = "http://localhost:3000"
GRAFANA_AUTH = base64.b64encode(b"admin:admin").decode()
DASHBOARD_UID = "nautobuff-telemetry"  # set by pilot.py on first run; adwlqs9 legacy


def _get_clab_mgmt_ips():
    """
    Return a dict mapping node shortname (e.g. 'R1') to its containerlab
    management IPv4 address, by parsing `containerlab inspect --all --format json`.
    Returns empty dict if containerlab is not available or no lab is running.
    """
    try:
        result = subprocess.run(
            ["sudo", "containerlab", "inspect", "--all", "--format", "json"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return {}
        data = json.loads(result.stdout or "{}")
        # data is {"lab_name": [container, ...], ...}
        if isinstance(data, dict):
            containers = [c for lab in data.values() for c in (lab if isinstance(lab, list) else [])]
        else:
            containers = data
        ip_map = {}
        for container in containers:
            name = container.get("name", "")          # e.g. "clab-test-R1"
            ipv4 = container.get("ipv4_address", "")  # e.g. "172.20.20.3/24"
            if not name or not ipv4:
                continue
            # strip prefix length
            ip = ipv4.split("/")[0]
            # extract shortname: last segment after final '-'
            shortname = name.rsplit("-", 1)[-1]
            ip_map[shortname] = ip
        return ip_map
    except Exception:
        return {}


def update_gnmic_yaml_from_hosts():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    ipam_dir = os.path.join(script_dir, "..", "IPAM")
    gnmi_path = os.path.join(script_dir, "..", "..")

    hosts_csv = os.path.join(ipam_dir, "hosts.csv")
    gnmic_yaml = os.path.abspath(os.path.join(gnmi_path, "gnmic-stream.yaml"))

    if not os.path.exists(gnmic_yaml):
        print(f"[⚠] gnmic-stream.yaml not found at {gnmic_yaml} — skipping gNMI update")
        return

    clab_ips = _get_clab_mgmt_ips()
    if clab_ips:
        print(f"[✔] Using containerlab management IPs for {list(clab_ips.keys())}")
    else:
        print("[⚠] Could not get containerlab IPs — falling back to hosts.csv management_ip")

    try:
        with open(gnmic_yaml, "r") as f:
            config = yaml.safe_load(f) or {}

        config["targets"] = {}
        subscription_keys = list((config.get("subscriptions") or {}).keys())

        if os.path.exists(hosts_csv):
            with open(hosts_csv, newline="") as csvfile:
                reader = csv.DictReader(csvfile)
                for row in reader:
                    hostname = row.get("hostname", "").strip()
                    mgmt_ip = row.get("management_ip", "").strip()
                    username = row.get("username", "").strip()
                    password = row.get("password", "").strip()
                    if not hostname:
                        continue
                    ip = clab_ips.get(hostname) or mgmt_ip
                    if not ip:
                        continue
                    target = {"subscriptions": subscription_keys}
                    if username:
                        target["username"] = username
                    if password:
                        target["password"] = password
                    config["targets"][f"{ip}:6030"] = target
        elif clab_ips:
            # hosts.csv not yet populated — use containerlab IPs with default cEOS credentials
            print("[ℹ] hosts.csv not found — using containerlab IPs with default credentials")
            for hostname, ip in clab_ips.items():
                config["targets"][f"{ip}:6030"] = {
                    "subscriptions": subscription_keys,
                    "username": "admin",
                    "password": "admin",
                }
        else:
            print("[⚠] No hosts.csv and no containerlab IPs — skipping gNMI update")
            return

        # Remove stale processors section if present
        config.pop("processors", None)
        if "outputs" in config and "influxdb" in config["outputs"]:
            config["outputs"]["influxdb"].pop("event-processors", None)

        with open(gnmic_yaml, "w") as f:
            yaml.dump(config, f, sort_keys=False)

        print(f"[✔] gnmic-stream.yaml updated with {len(config['targets'])} target(s)")

        # Build hostname→IP:port map from what we just wrote, then sync Grafana
        hostname_ip_map = {}
        if os.path.exists(hosts_csv):
            with open(hosts_csv, newline="") as csvfile2:
                for row in csv.DictReader(csvfile2):
                    hostname = row.get("hostname", "").strip()
                    mgmt_ip = row.get("management_ip", "").strip()
                    if not hostname:
                        continue
                    ip = clab_ips.get(hostname) or mgmt_ip
                    if ip:
                        hostname_ip_map[hostname] = f"{ip}:6030"
        else:
            hostname_ip_map = {h: f"{ip}:6030" for h, ip in clab_ips.items()}
        _sync_grafana_dashboard(hostname_ip_map)

    except Exception as e:
        print(f"[⚠] Failed to update gnmic-stream.yaml: {e}")


def _sync_grafana_dashboard(hostname_ip_map):
    """
    Patch the NAutoBuff Grafana dashboard so the Device variable and Flux
    device-name mapper reflect the current hostname→IP mapping from hosts.csv
    + containerlab.  Safe to call repeatedly — skipped silently if Grafana
    is unreachable or the dashboard doesn't exist yet.
    """
    if not hostname_ip_map:
        return

    headers = {
        "Content-Type": "application/json",
        "Authorization": "Basic " + GRAFANA_AUTH,
    }

    # Fetch current dashboard JSON
    try:
        req = urllib.request.Request(
            f"{GRAFANA_URL}/api/dashboards/uid/{DASHBOARD_UID}",
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            payload = json.loads(resp.read())
    except Exception:
        return  # Grafana not up or dashboard not yet created

    dashboard = payload.get("dashboard", {})
    folderId = payload.get("meta", {}).get("folderId", 0)

    # --- Update Device variable options ---
    var_query = ", ".join(f"{name} : {ip}" for name, ip in hostname_ip_map.items())
    all_value = "|".join(re.escape(ip) for ip in hostname_ip_map.values())
    var_options = [{"text": "All", "value": "$__all", "selected": True}]
    for name, ip in hostname_ip_map.items():
        var_options.append({"text": name, "value": ip, "selected": False})

    for var in dashboard.get("templating", {}).get("list", []):
        if var.get("name") == "hostname":
            var["query"] = var_query
            var["options"] = var_options
            var["allValue"] = all_value
            var["current"] = {"text": ["All"], "value": ["$__all"]}
            break

    # --- Update Flux DEV mapper in every panel query ---
    cases = " ".join(
        f'else if r.source == "{ip}" then "{name}"'
        for name, ip in hostname_ip_map.items()
    )
    new_dev = f'(if false then "" {cases} else r.source)'

    # Replace any existing DEV expression (pattern: starts with "(if false then" or
    # legacy hardcoded "if r.source ==")
    dev_pattern = re.compile(
        r'\(if false then "".*?else r\.source\)'
        r'|\(if r\.source ==.*?else r\.source\)',
        re.DOTALL,
    )

    def patch_query(q):
        return dev_pattern.sub(new_dev, q)

    for panel in dashboard.get("panels", []):
        for t in panel.get("targets", []):
            if "query" in t:
                t["query"] = patch_query(t["query"])
        # recurse into collapsed rows
        for inner in panel.get("panels", []):
            for t in inner.get("targets", []):
                if "query" in t:
                    t["query"] = patch_query(t["query"])

    # Post updated dashboard back
    try:
        body = json.dumps({
            "overwrite": True,
            "folderId": folderId,
            "dashboard": dashboard,
        }).encode()
        req = urllib.request.Request(
            f"{GRAFANA_URL}/api/dashboards/db",
            data=body,
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8):
            pass
        print(f"[✔] Grafana dashboard variable synced for {list(hostname_ip_map.keys())}")
    except Exception as e:
        print(f"[⚠] Could not sync Grafana dashboard: {e}")

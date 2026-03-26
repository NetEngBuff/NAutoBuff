import csv
import json
import subprocess
import yaml
import os


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

    if not os.path.exists(hosts_csv):
        print(f"[⚠] hosts.csv not found — skipping gNMI update")
        return

    # Prefer containerlab management IPs (Management0) over hosts.csv in-band IPs
    clab_ips = _get_clab_mgmt_ips()
    if clab_ips:
        print(f"[✔] Using containerlab management IPs for {list(clab_ips.keys())}")
    else:
        print("[⚠] Could not get containerlab IPs — falling back to hosts.csv management_ip")

    try:
        with open(gnmic_yaml, "r") as f:
            config = yaml.safe_load(f) or {}

        config["targets"] = {}

        with open(hosts_csv, newline="") as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                hostname = row.get("hostname", "").strip()
                mgmt_ip = row.get("management_ip", "").strip()
                username = row.get("username", "").strip()
                password = row.get("password", "").strip()
                if not hostname:
                    continue
                # Use containerlab mgmt IP if available, else fall back to hosts.csv
                ip = clab_ips.get(hostname) or mgmt_ip
                if not ip:
                    continue
                target = {
                    "subscriptions": list((config.get("subscriptions") or {}).keys()),
                }
                if username:
                    target["username"] = username
                if password:
                    target["password"] = password
                # gnmic uses the map key as the address — use IP:port directly
                config["targets"][f"{ip}:6030"] = target

        # Remove stale processors section if present
        config.pop("processors", None)
        if "outputs" in config and "influxdb" in config["outputs"]:
            config["outputs"]["influxdb"].pop("event-processors", None)

        with open(gnmic_yaml, "w") as f:
            yaml.dump(config, f, sort_keys=False)

        print(f"[✔] gnmic-stream.yaml updated with {len(config['targets'])} target(s)")
    except Exception as e:
        print(f"[⚠] Failed to update gnmic-stream.yaml: {e}")

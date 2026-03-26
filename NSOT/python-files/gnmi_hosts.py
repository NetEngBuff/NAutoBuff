import csv
import yaml
import os


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

    try:
        with open(gnmic_yaml, "r") as f:
            config = yaml.safe_load(f) or {}

        config["targets"] = {}

        with open(hosts_csv, newline="") as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                hostname = row.get("hostname", "").strip()
                mgmt_ip = row.get("management_ip", "").strip()
                if hostname and mgmt_ip:
                    config["targets"][hostname] = {"address": f"{mgmt_ip}:6030"}

        with open(gnmic_yaml, "w") as f:
            yaml.dump(config, f, sort_keys=False)

        print(f"[✔] gnmic-stream.yaml updated with {len(config['targets'])} target(s)")
    except Exception as e:
        print(f"[⚠] Failed to update gnmic-stream.yaml: {e}")

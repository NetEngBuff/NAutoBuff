import os
import pathlib
import subprocess
import getpass
import json
import time
import urllib.request
import urllib.error
import base64


def find_base_path():
    current_dir = pathlib.Path(__file__).parent.absolute()
    base_path = current_dir
    while base_path.name != "NAutoHUB" and base_path.parent != base_path:
        base_path = base_path.parent
    if base_path.name != "NAutoHUB":
        raise Exception("Could not find 'NAutoHUB' in the path hierarchy")
    return base_path


def get_service_user():
    return "root" if os.geteuid() == 0 else getpass.getuser()


def create_service_or_timer_file(file_name, file_content):
    file_path = f"/etc/systemd/system/{file_name}"
    try:
        subprocess.run(
            ["sudo", "tee", file_path],
            input=file_content.encode(),
            check=True,
            stdout=subprocess.DEVNULL,
        )
        print(f"✅ Created: {file_path}")
    except subprocess.CalledProcessError:
        print(f"❌ Failed to create {file_name}. Do you have sudo privileges?")
    except Exception as e:
        print(f"❌ Error creating {file_name}: {e}")


def deploy():
    try:
        subprocess.run(["sudo", "systemctl", "daemon-reload"], check=True)
        print("🔁 systemd daemon reloaded")
    except subprocess.CalledProcessError:
        print("❌ Failed to reload systemd daemon.")
        return

    services = [
        "password_update.service",
        "device_health_check.timer",
        "device_health_check.service",
        "ipam.service",
        "ngrok.service",
        "gnmic_nautohub.service",
    ]

    for service in services:
        try:
            subprocess.run(["sudo", "systemctl", "enable", service], check=True)
            subprocess.run(["sudo", "systemctl", "start", service], check=True)
            print(f"✅ Enabled & started: {service}")
        except subprocess.CalledProcessError:
            print(f"❌ Failed to enable/start {service}")


def setup_monitoring(base_path):
    """
    Initialize InfluxDB, generate gnmic-stream.yaml with a real token,
    and configure the Grafana datasource automatically.
    """
    print("\n[INFO] Setting up monitoring stack (InfluxDB → gnmic → Grafana)...")

    # ── 1. Wait for InfluxDB ──────────────────────────────────────────────────
    for attempt in range(15):
        ping = subprocess.run(["influx", "ping"], capture_output=True, text=True)
        if ping.returncode == 0:
            break
        print(f"  Waiting for InfluxDB ({attempt + 1}/15)...")
        time.sleep(2)
    else:
        print("❌ InfluxDB is not responding. Skipping monitoring setup.")
        return

    # ── 2. Initialize InfluxDB (idempotent — skipped if already set up) ───────
    setup = subprocess.run(
        ["influx", "setup",
         "--username", "admin",
         "--password", "NAutoHUB123!",
         "--org", "NAutoHUB",
         "--bucket", "NAutoHUB",
         "--retention", "0",
         "--force"],
        capture_output=True, text=True,
    )
    if setup.returncode == 0:
        print("✅ InfluxDB initialized  (admin / NAutoHUB123!)")
    else:
        print("ℹ️  InfluxDB already initialized, skipping setup")

    # ── 3. Get or create a gnmic API token ────────────────────────────────────
    token = None

    list_result = subprocess.run(
        ["influx", "auth", "list", "--org", "NAutoHUB", "--json"],
        capture_output=True, text=True,
    )
    if list_result.returncode == 0:
        try:
            for auth in json.loads(list_result.stdout or "[]"):
                if "gnmic" in auth.get("description", "").lower():
                    token = auth.get("token")
                    print("ℹ️  Reusing existing gnmic token")
                    break
        except (json.JSONDecodeError, KeyError):
            pass

    if not token:
        create = subprocess.run(
            ["influx", "auth", "create",
             "--org", "NAutoHUB",
             "--all-access",
             "--description", "gnmic-nautohub",
             "--json"],
            capture_output=True, text=True,
        )
        if create.returncode == 0:
            try:
                data = json.loads(create.stdout)
                token = (data[0] if isinstance(data, list) else data).get("token")
            except (json.JSONDecodeError, IndexError, KeyError):
                pass

    if not token:
        print("❌ Could not obtain InfluxDB token — set it manually in gnmic-stream.yaml")
        return

    # ── 4. Write gnmic-stream.yaml from the example template ─────────────────
    example = base_path / "gnmic-stream.yaml.example"
    output  = base_path / "gnmic-stream.yaml"
    if example.exists():
        output.write_text(example.read_text().replace("INFLUXDB_TOKEN_HERE", token))
        print("✅ gnmic-stream.yaml generated with InfluxDB token")
    else:
        print("❌ gnmic-stream.yaml.example not found — cannot generate config")
        return

    # ── 5. Configure Grafana datasource via HTTP API ──────────────────────────
    print("[INFO] Waiting for Grafana to be ready...")
    time.sleep(6)

    ds_payload = json.dumps({
        "name": "NAutoHUB-InfluxDB",
        "type": "influxdb",
        "access": "proxy",
        "url": "http://localhost:8086",
        "jsonData": {
            "version": "Flux",
            "organization": "NAutoHUB",
            "defaultBucket": "NAutoHUB",
        },
        "secureJsonData": {"token": token},
    }).encode()

    try:
        req = urllib.request.Request(
            "http://localhost:3000/api/datasources",
            data=ds_payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Basic " + base64.b64encode(b"admin:admin").decode(),
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8):
            print("✅ Grafana datasource 'NAutoHUB-InfluxDB' created")
    except urllib.error.HTTPError as e:
        if e.code == 409:
            print("ℹ️  Grafana datasource already exists")
        else:
            print(f"ℹ️  Grafana API returned {e.code} — configure datasource manually at http://localhost:3000")
    except Exception as e:
        print(f"ℹ️  Grafana not reachable: {e}")
        print("   → After Grafana starts, add a Flux datasource pointing to http://localhost:8086")
        print(f"   → Use org: NAutoHUB  bucket: NAutoHUB  token: {token[:20]}...")

    print("\n✅ Monitoring setup complete.")
    print("   InfluxDB : http://localhost:8086  (admin / NAutoHUB123!)")
    print("   Grafana  : http://localhost:3000  (admin / admin)")
    print("   → Build dashboards in Grafana using the 'NAutoHUB-InfluxDB' datasource")


def main():
    base_path = find_base_path()
    service_user = get_service_user()

    services_and_timers = [
        {
            "file_name": "password_update.service",
            "content": f"""[Unit]
Description=Device Password Update Service

[Service]
ExecStart={base_path}/pilot-config/venv/bin/python {base_path}/NSOT/python-files/password_reset.py
WorkingDirectory={base_path}/NSOT/python-files/
Restart=always
User={service_user}

[Install]
WantedBy=multi-user.target
""",
        },
        {
            "file_name": "device_health_check.timer",
            "content": """[Unit]
Description=Run Device Health Check Every Hour

[Timer]
OnCalendar=hourly
Persistent=true

[Install]
WantedBy=timers.target
""",
        },
        {
            "file_name": "device_health_check.service",
            "content": f"""[Unit]
Description=Device Health Check Service
After=network.target

[Service]
WorkingDirectory={base_path}/NSOT/python-files
ExecStart=/bin/bash -c '{base_path}/pilot-config/venv/bin/python {base_path}/NSOT/python-files/health_checks.py'
Restart=on-failure
User={service_user}

[Install]
WantedBy=multi-user.target
""",
        },
        {
            "file_name": "ipam.service",
            "content": f"""[Unit]
Description=IPAM Python Service
After=network.target

[Service]
ExecStart={base_path}/pilot-config/venv/bin/python {base_path}/NSOT/python-files/ipam.py
WorkingDirectory={base_path}/NSOT
Restart=always
User={service_user}
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
""",
        },
        {
            "file_name": "ngrok.service",
            "content": f"""[Unit]
Description=Ngrok HTTP Tunnel Service
After=network.target

[Service]
Type=simple
ExecStart=/usr/local/bin/ngrok start --all --config={base_path}/NSOT/misc/ngrok_config.yml --log={base_path}/NSOT/logs/ngrok.log
WorkingDirectory={base_path}
Restart=on-failure
User={service_user}

[Install]
WantedBy=multi-user.target
""",
        },
        {
            "file_name": "gnmic_nautohub.service",
            "content": f"""[Unit]
Description=gNMIc Stream Collector Service
After=network.target docker.service
Requires=docker.service

[Service]
ExecStart=/usr/local/bin/gnmic subscribe --config {base_path}/gnmic-stream.yaml
WorkingDirectory={base_path}
Restart=always
User={service_user}

[Install]
WantedBy=multi-user.target
""",
        },
    ]

    for item in services_and_timers:
        create_service_or_timer_file(item["file_name"], item["content"])

    deploy()


if __name__ == "__main__":
    subprocess.run(["sudo", "systemctl", "enable", "jenkins"], check=True)
    subprocess.run(["sudo", "systemctl", "start", "jenkins"], check=True)
    print("✅ Jenkins enabled and started")
    main()
    base = find_base_path()
    setup_monitoring(base)

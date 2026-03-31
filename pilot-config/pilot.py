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


def _build_grafana_dashboard(ds_uid, hostname_ip_map=None):
    """
    Return the full Grafana dashboard JSON for NAutoHUB telemetry.

    hostname_ip_map: dict of {hostname: "IP:port"}, e.g. {"R1": "172.20.20.3:6030"}.
    Built dynamically from hosts.csv + containerlab IPs — nothing hardcoded.
    If empty (topology not yet deployed), the Device variable has no options yet;
    gnmi_hosts.py re-syncs it after each topology deploy.
    """
    if hostname_ip_map is None:
        hostname_ip_map = {}

    def ds():
        return {"type": "influxdb", "uid": ds_uid}

    def target(ref, query):
        return {"refId": ref, "datasource": ds(), "query": query}

    def timeseries(pid, title, x, y, w, h, query, unit="short"):
        return {
            "id": pid, "type": "timeseries", "title": title,
            "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "datasource": ds(),
            "fieldConfig": {
                "defaults": {"unit": unit,
                             "custom": {"lineWidth": 2, "fillOpacity": 5}},
                "overrides": []},
            "options": {
                "tooltip": {"mode": "multi", "sort": "desc"},
                "legend": {"displayMode": "table", "placement": "bottom"}},
            "targets": [target("A", query)],
        }

    def table(pid, title, x, y, w, h, query):
        return {
            "id": pid, "type": "table", "title": title,
            "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "datasource": ds(),
            "fieldConfig": {"defaults": {}, "overrides": []},
            "options": {"cellHeight": "sm", "footer": {"show": False}},
            "targets": [target("A", query)],
        }

    def stat(pid, title, x, y, w, h, query, unit="percent"):
        return {
            "id": pid, "type": "stat", "title": title,
            "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "datasource": ds(),
            "fieldConfig": {
                "defaults": {
                    "unit": unit,
                    "thresholds": {"mode": "absolute", "steps": [
                        {"color": "green", "value": None},
                        {"color": "yellow", "value": 60},
                        {"color": "red", "value": 85}]},
                    "mappings": []},
                "overrides": []},
            "options": {
                "reduceOptions": {"calcs": ["lastNotNull"]},
                "colorMode": "background", "graphMode": "area"},
            "targets": [target("A", query)],
        }

    def row(pid, title, y):
        return {"id": pid, "type": "row", "title": title,
                "gridPos": {"x": 0, "y": y, "w": 24, "h": 1},
                "collapsed": False}

    B = "NAutoHUB"
    SF = '|> filter(fn: (r) => r.source =~ /^${hostname:regex}$/)'

    # Build Flux inline device-name mapper from the live hostname→IP map.
    # Falls back to r.source (shows IP:port) when no topology is deployed yet.
    if hostname_ip_map:
        cases = " ".join(
            f'else if r.source == "{ip}" then "{name}"'
            for name, ip in hostname_ip_map.items()
        )
        DEV = f'(if false then "" {cases} else r.source)'
    else:
        DEV = "r.source"

    q_cpu = (
        f'from(bucket: "{B}")\n'
        f'  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n'
        f'  |> filter(fn: (r) => r._measurement == "device-health")\n'
        f'  |> filter(fn: (r) => r._field == "/components/component/cpu/utilization/state/instant")\n'
        f'  {SF}\n'
        f'  |> group(columns: ["source", "_time"])\n'
        f'  |> mean()\n'
        f'  |> group(columns: ["source"])\n'
        f'  |> map(fn: (r) => ({{r with _field: {DEV}}}))'
    )

    def counter_rate(field, exclude_mgmt=True):
        mgmt_filter = '  |> filter(fn: (r) => r.interface_name !~ /^Management/)\n' if exclude_mgmt else ''
        return (
            f'from(bucket: "{B}")\n'
            f'  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n'
            f'  |> filter(fn: (r) => r._measurement == "interface-counters")\n'
            f'  |> filter(fn: (r) => r._field == "/interfaces/interface/state/counters/{field}")\n'
            f'{mgmt_filter}'
            f'  {SF}\n'
            f'  |> derivative(unit: 1s, nonNegative: true)\n'
            f'  |> map(fn: (r) => ({{r with _field: {DEV} + "/" + r.interface_name}}))'
        )

    q_status = (
        f'from(bucket: "{B}")\n'
        f'  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n'
        f'  |> filter(fn: (r) => r._measurement == "interface-status")\n'
        f'  {SF}\n'
        f'  |> last()\n'
        f'  |> map(fn: (r) => ({{r with source: {DEV}}}))\n'
        f'  |> pivot(rowKey: ["source", "interface_name"], columnKey: ["_field"], valueColumn: "_value")\n'
        f'  |> rename(columns: {{source: "Device", interface_name: "Interface",'
        f' "/interfaces/interface/state/oper-status": "Oper Status",'
        f' "/interfaces/interface/state/admin-status": "Admin Status"}})\n'
        f'  |> keep(columns: ["Device", "Interface", "Oper Status", "Admin Status"])'
    )

    q_static = (
        f'from(bucket: "{B}")\n'
        f'  |> range(start: -2h)\n'
        f'  |> filter(fn: (r) => r._measurement == "interface-static")\n'
        f'  {SF}\n'
        f'  |> last()\n'
        f'  |> map(fn: (r) => ({{r with source: {DEV}}}))\n'
        f'  |> pivot(rowKey: ["source", "interface_name"], columnKey: ["_field"], valueColumn: "_value")\n'
        f'  |> rename(columns: {{source: "Device", interface_name: "Interface",'
        f' "/interfaces/interface/ethernet/state/mac-address": "MAC Address",'
        f' "/interfaces/interface/state/mtu": "MTU"}})\n'
        f'  |> keep(columns: ["Device", "Interface", "MAC Address", "MTU"])'
    )

    panels = [
        row(10, "Device Health", 0),
        stat(11, "CPU Utilization %", 0, 1, 24, 5, q_cpu, unit="percent"),

        row(20, "Interface Traffic", 6),
        timeseries(21, "Bandwidth In (bytes/s)", 0, 7, 12, 8,
                   counter_rate("in-octets"), unit="Bps"),
        timeseries(22, "Bandwidth Out (bytes/s)", 12, 7, 12, 8,
                   counter_rate("out-octets"), unit="Bps"),

        row(30, "Packet Rates", 15),
        timeseries(31, "Packets In / sec", 0, 16, 12, 8,
                   counter_rate("in-pkts"), unit="pps"),
        timeseries(32, "Packets Out / sec", 12, 16, 12, 8,
                   counter_rate("out-pkts"), unit="pps"),

        row(40, "Errors & Drops", 24),
        timeseries(41, "Interface Errors / sec", 0, 25, 12, 7,
                   counter_rate("in-errors"), unit="pps"),
        timeseries(42, "Interface Drops / sec", 12, 25, 12, 7,
                   counter_rate("in-discards"), unit="pps"),

        row(50, "Interface Status", 32),
        table(51, "Oper / Admin Status", 0, 33, 12, 8, q_status),
        table(52, "MAC Address & MTU", 12, 33, 12, 8, q_static),
    ]

    # Build the Device dropdown variable from the live hostname→IP map
    var_options = [{"text": "All", "value": "$__all", "selected": True}]
    for name, ip in hostname_ip_map.items():
        var_options.append({"text": name, "value": ip, "selected": False})

    import re as _re
    all_value = "|".join(_re.escape(ip) for ip in hostname_ip_map.values()) if hostname_ip_map else ".*"
    var_query = ", ".join(f"{name} : {ip}" for name, ip in hostname_ip_map.items())

    return {
        "uid": "nautohub-telemetry",
        "title": "NAutoHUB Network Telemetry",
        "tags": ["nautohub", "gnmi"],
        "editable": True,
        "graphTooltip": 1,
        "time": {"from": "now-30m", "to": "now"},
        "refresh": "10s",
        "templating": {
            "list": [{
                "name": "hostname",
                "label": "Device",
                "type": "custom",
                "query": var_query,
                "current": {"text": ["All"], "value": ["$__all"]},
                "options": var_options,
                "includeAll": True,
                "allValue": all_value,
                "multi": True,
                "hide": 0,
            }]
        },
        "panels": panels,
    }


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

    # ── 6. Create/update the Grafana telemetry dashboard ─────────────────────
    grafana_auth = base64.b64encode(b"admin:admin").decode()
    ds_uid = None
    try:
        req = urllib.request.Request(
            "http://localhost:3000/api/datasources/name/NAutoHUB-InfluxDB",
            headers={"Authorization": "Basic " + grafana_auth},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            ds_uid = json.loads(resp.read()).get("uid")
    except Exception:
        pass

    if ds_uid:
        # Build hostname→IP map from gnmi_hosts so the dashboard isn't hardcoded.
        # If topology isn't deployed yet the map will be empty; gnmi_hosts re-syncs
        # the dashboard after each deploy automatically.
        hostname_ip_map = {}
        try:
            import sys as _sys
            _sys.path.insert(0, str(base_path / "NSOT" / "python-files"))
            from gnmi_hosts import _get_clab_mgmt_ips, update_gnmic_yaml_from_hosts  # noqa: F401
            import csv as _csv
            clab_ips = _get_clab_mgmt_ips()
            hosts_csv = base_path / "NSOT" / "IPAM" / "hosts.csv"
            if hosts_csv.exists():
                with open(hosts_csv, newline="") as _f:
                    for row in _csv.DictReader(_f):
                        hostname = row.get("hostname", "").strip()
                        mgmt_ip = row.get("management_ip", "").strip()
                        if not hostname:
                            continue
                        ip = clab_ips.get(hostname) or mgmt_ip
                        if ip:
                            hostname_ip_map[hostname] = f"{ip}:6030"
        except Exception:
            pass  # topology not deployed yet — dashboard created with empty variable

        dashboard_payload = json.dumps({
            "overwrite": True,
            "dashboard": _build_grafana_dashboard(ds_uid, hostname_ip_map),
        }).encode()
        try:
            req = urllib.request.Request(
                "http://localhost:3000/api/dashboards/db",
                data=dashboard_payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Basic " + grafana_auth,
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read())
                url = result.get("url", "")
                print(f"✅ Grafana dashboard created: http://localhost:3000{url}")
        except Exception as e:
            print(f"ℹ️  Could not create Grafana dashboard: {e}")
    else:
        print("ℹ️  Grafana datasource UID not found — skipping dashboard creation")

    print("\n✅ Monitoring setup complete.")
    print("   InfluxDB : http://localhost:8086  (admin / NAutoHUB123!)")
    print("   Grafana  : http://localhost:3000  (admin / admin)")


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
ExecStart=/usr/local/bin/gnmic subscribe --log --config {base_path}/gnmic-stream.yaml
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


def provision_jenkins_token():
    """
    Wait for Jenkins to be ready, generate an API token using the initial admin
    password, and write it into run_nahub.sh automatically.
    Skips gracefully if Jenkins is unreachable or already has a token set.
    """
    jenkins_url = "http://localhost:8080"
    run_nahub = os.path.join(os.path.dirname(__file__), "run_nahub.sh")
    token_marker = "JENKINS_TOKEN:-"

    # Skip if a non-empty token is already baked into run_nahub.sh
    if os.path.exists(run_nahub):
        with open(run_nahub) as f:
            content = f.read()
        import re as _re
        m = _re.search(r'JENKINS_TOKEN:-([^"}]+)', content)
        if m and m.group(1).strip():
            print(f"✅ Jenkins token already present in run_nahub.sh — skipping generation")
            return

    # Read the initial admin password
    init_pass_path = "/var/lib/jenkins/secrets/initialAdminPassword"
    try:
        result = subprocess.run(
            ["sudo", "cat", init_pass_path],
            capture_output=True, text=True, check=True,
        )
        password = result.stdout.strip()
    except Exception:
        print("⚠️  Could not read Jenkins initial password — skipping token generation")
        return

    # Wait up to 90 s for Jenkins to respond
    print("⏳ Waiting for Jenkins to be ready…")
    auth_b64 = base64.b64encode(f"admin:{password}".encode()).decode()
    headers = {"Authorization": f"Basic {auth_b64}", "Content-Type": "application/x-www-form-urlencoded"}
    for _ in range(18):
        try:
            req = urllib.request.Request(f"{jenkins_url}/api/json", headers=headers)
            urllib.request.urlopen(req, timeout=5)
            break
        except Exception:
            time.sleep(5)
    else:
        print("⚠️  Jenkins did not become ready in time — skipping token generation")
        return

    # Fetch CSRF crumb
    try:
        req = urllib.request.Request(f"{jenkins_url}/crumbIssuer/api/json", headers=headers)
        with urllib.request.urlopen(req, timeout=10) as r:
            crumb_data = json.loads(r.read())
        crumb_header = crumb_data["crumbRequestField"]
        crumb_value = crumb_data["crumb"]
        headers[crumb_header] = crumb_value
    except Exception as e:
        print(f"⚠️  Could not fetch Jenkins crumb: {e} — skipping token generation")
        return

    # Generate API token
    try:
        body = "newTokenName=NAutoHUB".encode()
        req = urllib.request.Request(
            f"{jenkins_url}/user/admin/descriptorByName/jenkins.security.ApiTokenProperty/generateNewToken",
            data=body, headers=headers, method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            resp = json.loads(r.read())
        token = resp["data"]["tokenValue"]
    except Exception as e:
        print(f"⚠️  Could not generate Jenkins API token: {e}")
        return

    # Write token into run_nahub.sh
    if not os.path.exists(run_nahub):
        print(f"⚠️  {run_nahub} not found — cannot write token")
        return

    with open(run_nahub) as f:
        content = f.read()

    import re as _re
    new_content = _re.sub(
        r'(export JENKINS_TOKEN="\$\{JENKINS_TOKEN:-)[^"}]*("\}")',
        rf'\g<1>{token}\2',
        content,
    )
    # Handle the simpler no-default form too
    new_content = _re.sub(
        r'(export JENKINS_TOKEN="\$\{JENKINS_TOKEN:-)[^"]*(")',
        rf'\g<1>{token}\2',
        new_content,
    )

    with open(run_nahub, "w") as f:
        f.write(new_content)

    print(f"✅ Jenkins API token generated and saved to run_nahub.sh")


if __name__ == "__main__":
    subprocess.run(["sudo", "systemctl", "enable", "jenkins"], check=True)
    subprocess.run(["sudo", "systemctl", "start", "jenkins"], check=True)
    print("✅ Jenkins enabled and started")
    provision_jenkins_token()
    main()
    base = find_base_path()
    setup_monitoring(base)

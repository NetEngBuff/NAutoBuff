import os
import pathlib
import shutil
import subprocess
import getpass
import json
import time
import urllib.request
import urllib.error
import base64
import http.cookiejar
import urllib.parse


def find_base_path():
    base_path = pathlib.Path(__file__).resolve().parent.parent
    required_paths = [
        base_path / "pilot-config",
        base_path / "NSOT" / "python-files",
        base_path / "NSOT" / "GUI" / "flask_app",
    ]
    missing = [str(path) for path in required_paths if not path.exists()]
    if missing:
        raise Exception(
            "Could not identify the project root from pilot.py; missing: "
            + ", ".join(missing)
        )
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


def _gnmic_has_targets(base_path):
    gnmic_yaml = base_path / "gnmic-stream.yaml"
    if not gnmic_yaml.exists():
        return False
    try:
        import yaml
        config = yaml.safe_load(gnmic_yaml.read_text()) or {}
        return bool(config.get("targets"))
    except Exception:
        return False


def deploy():
    try:
        subprocess.run(["sudo", "systemctl", "daemon-reload"], check=True)
        print("🔁 systemd daemon reloaded")
    except subprocess.CalledProcessError:
        print("❌ Failed to reload systemd daemon.")
        return

    base_path = find_base_path()
    services = [
        "password_update.service",
        "device_health_check.timer",
        "device_health_check.service",
        "ipam.service",
        "ngrok.service",
        "gnmic_nautobuff.service",
    ]

    for service in services:
        try:
            subprocess.run(["sudo", "systemctl", "enable", service], check=True)
            if service == "gnmic_nautobuff.service" and not _gnmic_has_targets(base_path):
                subprocess.run(["sudo", "systemctl", "reset-failed", service], check=False)
                print(f"✅ Enabled only: {service} (starts after topology targets exist)")
                continue
            subprocess.run(["sudo", "systemctl", "start", service], check=True)
            print(f"✅ Enabled & started: {service}")
        except subprocess.CalledProcessError:
            print(f"❌ Failed to enable/start {service}")


def _influx_request(opener, url, method="GET", data=None, headers=None, timeout=10):
    body = None
    if data is not None:
        body = json.dumps(data).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers=headers or {},
        method=method,
    )
    with opener.open(req, timeout=timeout) as response:
        payload = response.read().decode()
    return json.loads(payload) if payload else {}


def _create_influx_token_via_http():
    """
    Influx CLI auth commands require an existing local token. On a clean host that
    was initialized previously, the server can be ready while the CLI config is
    missing. Use the local admin credentials to create the gNMI/Grafana token.
    """
    base_url = "http://localhost:8086"
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    signin_headers = {
        "Authorization": "Basic " + base64.b64encode(b"admin:admin123").decode(),
    }
    signin = urllib.request.Request(
        f"{base_url}/api/v2/signin",
        headers=signin_headers,
        method="POST",
    )
    opener.open(signin, timeout=10).read()

    common_headers = {"Content-Type": "application/json"}
    orgs = _influx_request(
        opener,
        f"{base_url}/api/v2/orgs?org={urllib.parse.quote('NAutoBuff')}",
        headers=common_headers,
    )
    orgs_list = orgs.get("orgs", [])
    if not orgs_list:
        raise RuntimeError("InfluxDB org 'NAutoBuff' was not found")
    org_id = orgs_list[0]["id"]

    buckets = _influx_request(
        opener,
        f"{base_url}/api/v2/buckets?orgID={org_id}&name={urllib.parse.quote('NAutoBuff')}",
        headers=common_headers,
    )
    bucket_list = buckets.get("buckets", [])
    if not bucket_list:
        raise RuntimeError("InfluxDB bucket 'NAutoBuff' was not found")
    bucket_id = bucket_list[0]["id"]

    auth = _influx_request(
        opener,
        f"{base_url}/api/v2/authorizations",
        method="POST",
        data={
            "orgID": org_id,
            "description": "gnmic-nautobuff",
            "permissions": [
                {"action": "read", "resource": {"type": "buckets", "id": bucket_id, "orgID": org_id}},
                {"action": "write", "resource": {"type": "buckets", "id": bucket_id, "orgID": org_id}},
            ],
        },
        headers=common_headers,
    )
    token = auth.get("token")
    if not token:
        raise RuntimeError("InfluxDB did not return a token")
    return token

def _build_grafana_dashboard(ds_uid, hostname_ip_map=None):
    """
    Return the full Grafana dashboard JSON for NAutoBuff telemetry.

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

    B = "NAutoBuff"
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
        "uid": "nautobuff-telemetry",
        "title": "NAutoBuff Network Telemetry",
        "tags": ["nautobuff", "gnmi"],
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
         "--password", "admin123",
         "--org", "NAutoBuff",
         "--bucket", "NAutoBuff",
         "--retention", "0",
         "--force"],
        capture_output=True, text=True,
    )
    if setup.returncode == 0:
        print("✅ InfluxDB initialized  (admin / admin123)")
    else:
        print("ℹ️  InfluxDB already initialized, skipping setup")

    # ── 3. Get or create a gnmic API token ────────────────────────────────────
    token = None

    list_result = subprocess.run(
        ["influx", "auth", "list", "--org", "NAutoBuff", "--json"],
        capture_output=True, text=True,
    )
    if list_result.returncode == 0:
        try:
            for auth in json.loads(list_result.stdout or "[]"):
                if "gnmic" in auth.get("description", "").lower():
                    candidate = auth.get("token")
                    # Validate the token actually works before reusing it
                    check = subprocess.run(
                        ["influx", "bucket", "list", "--org", "NAutoBuff",
                         "--token", candidate],
                        capture_output=True, text=True,
                    )
                    if check.returncode == 0:
                        token = candidate
                        print("ℹ️  Reusing existing gnmic token")
                    else:
                        print("ℹ️  Existing gnmic token is invalid — generating a new one")
                    break
        except (json.JSONDecodeError, KeyError):
            pass

    if not token:
        create = subprocess.run(
            ["influx", "auth", "create",
             "--org", "NAutoBuff",
             "--all-access",
             "--description", "gnmic-nautobuff",
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
        try:
            token = _create_influx_token_via_http()
            print("✅ InfluxDB token created via local admin login")
        except Exception as e:
            print(f"⚠️  InfluxDB HTTP token fallback failed: {e}")

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
        "name": "NAutoBuff-InfluxDB",
        "type": "influxdb",
        "access": "proxy",
        "url": "http://localhost:8086",
        "jsonData": {
            "version": "Flux",
            "organization": "NAutoBuff",
            "defaultBucket": "NAutoBuff",
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
            print("✅ Grafana datasource 'NAutoBuff-InfluxDB' created")
    except urllib.error.HTTPError as e:
        if e.code == 409:
            print("ℹ️  Grafana datasource already exists")
        else:
            print(f"ℹ️  Grafana API returned {e.code} — configure datasource manually at http://localhost:3000")
    except Exception as e:
        print(f"ℹ️  Grafana not reachable: {e}")
        print("   → After Grafana starts, add a Flux datasource pointing to http://localhost:8086")
        print(f"   → Use org: NAutoBuff  bucket: NAutoBuff  token: {token[:20]}...")

    # ── 6. Create/update the Grafana telemetry dashboard ─────────────────────
    grafana_auth = base64.b64encode(b"admin:admin").decode()
    ds_uid = None
    try:
        req = urllib.request.Request(
            "http://localhost:3000/api/datasources/name/NAutoBuff-InfluxDB",
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
    print("   InfluxDB : http://localhost:8086  (admin / admin123)")
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
            "file_name": "gnmic_nautobuff.service",
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

    # MCP HTTP servers — only if user opted in during requirements.sh
    mcp_flag = base_path / "NSOT" / "misc" / ".mcp_http_enabled"
    if mcp_flag.exists():
        print("\n[INFO] MCP HTTP mode enabled — creating services...")
        mcp_services = [
            {
                "file_name": "nautobuff_mcp_query.service",
                "script": "NSOT/mcp/dut_query.py",
                "port": 8001,
                "description": "NAutoBuff MCP Query Server",
            },
            {
                "file_name": "nautobuff_mcp_config.service",
                "script": "NSOT/mcp/dut_config.py",
                "port": 8002,
                "description": "NAutoBuff MCP Config Server",
            },
        ]
        python_bin = f"{base_path}/pilot-config/venv/bin/python"
        for svc in mcp_services:
            content = f"""[Unit]
Description={svc['description']}
After=network.target

[Service]
ExecStart={python_bin} {base_path}/{svc['script']} --http --port {svc['port']}
WorkingDirectory={base_path}
Restart=on-failure
User={service_user}
Environment=PYTHONPATH={base_path}/NSOT/mcp

[Install]
WantedBy=multi-user.target
"""
            create_service_or_timer_file(svc["file_name"], content)
            try:
                subprocess.run(["sudo", "systemctl", "enable", svc["file_name"]], check=True)
                subprocess.run(["sudo", "systemctl", "start", svc["file_name"]], check=True)
                print(f"✅ Enabled & started: {svc['file_name']} (port {svc['port']})")
            except subprocess.CalledProcessError:
                print(f"⚠️  Failed to start {svc['file_name']}")
    else:
        print("\nℹ️  MCP HTTP servers not enabled — Claude Code via SSH uses .mcp.json (no ports needed)")

    # Enable Ollama only if user opted in during requirements.sh
    ollama_flag = base_path / "NSOT" / "misc" / ".ollama_enabled"
    if ollama_flag.exists():
        print("\n[INFO] Ollama enabled — starting ollama.service...")
        try:
            subprocess.run(["sudo", "systemctl", "enable", "ollama"], check=True)
            subprocess.run(["sudo", "systemctl", "start", "ollama"], check=True)
            print("✅ Enabled & started: ollama.service (AI chatbot)")
        except subprocess.CalledProcessError:
            print("⚠️  Could not enable ollama.service — start it manually with: sudo systemctl start ollama")
    else:
        print("\nℹ️  AI chatbot not enabled — skipping ollama.service")


def provision_jenkins_firsttime():
    """
    Idempotent first-time Jenkins setup:
      1. Stops Jenkins
      2. Writes wizard-skip state files
      3. Writes Groovy init script to create admin/admin
      4. Enables and starts Jenkins
    Safe to re-run — resets the local admin account to admin/admin.
    """
    groovy_dir = "/var/lib/jenkins/init.groovy.d"
    groovy_file = os.path.join(groovy_dir, "01-security.groovy")

    print("⏳ Configuring Jenkins for first-time setup...")

    # Stop Jenkins so we can configure before it writes anything
    subprocess.run(["sudo", "systemctl", "stop", "jenkins"], capture_output=True)

    # 1. Skip the setup wizard
    jenkins_ver = subprocess.run(
        ["dpkg", "-l", "jenkins"],
        capture_output=True, text=True
    ).stdout
    ver = ""
    for line in jenkins_ver.splitlines():
        if line.startswith("ii"):
            ver = line.split()[2]
            break
    if ver:
        subprocess.run(
            ["sudo", "bash", "-c",
             "mkdir -p /var/lib/jenkins /var/cache/jenkins && "
             "chown jenkins:jenkins /var/lib/jenkins /var/cache/jenkins"],
            check=True
        )
        for state_file in [
            "/var/lib/jenkins/jenkins.install.UpgradeWizard.state",
            "/var/lib/jenkins/jenkins.install.InstallUtil.lastExecVersion",
        ]:
            subprocess.run(
                ["sudo", "bash", "-c", f"echo '{ver}' > {state_file} && chown jenkins:jenkins {state_file}"],
                check=True
            )

    # 2. Write Groovy init script to create admin/admin
    groovy_script = """\
import jenkins.model.*
import hudson.security.*
import jenkins.install.InstallState

def instance = Jenkins.get()
def realm = new HudsonPrivateSecurityRealm(false)
realm.createAccount("admin", "admin")
instance.setSecurityRealm(realm)
def strategy = new FullControlOnceLoggedInAuthorizationStrategy()
strategy.setAllowAnonymousRead(false)
instance.setAuthorizationStrategy(strategy)
instance.setInstallState(InstallState.INITIAL_SETUP_COMPLETED)
instance.save()
println("OK: Jenkins admin user reset and security configured")
"""
    subprocess.run(["sudo", "mkdir", "-p", groovy_dir], check=True)
    subprocess.run(
        ["sudo", "bash", "-c", f"cat > {groovy_file}"],
        input=groovy_script, text=True, check=True
    )
    subprocess.run(["sudo", "chown", "-R", "jenkins:jenkins", groovy_dir], check=True)

    # 3. Enable and start Jenkins
    subprocess.run(["sudo", "systemctl", "reset-failed", "jenkins"], check=False)
    subprocess.run(["sudo", "systemctl", "enable", "--now", "jenkins"], check=True)
    print("✅ Jenkins first-time configuration complete (credentials: admin / admin)")


def _jenkins_get_creds():
    """
    Return (jenkins_url, user, credential) ready to use for API calls.
    Checks .jenkins_creds first, then run_nautobuff.sh, then falls back
    to the initial admin password so callers work even before token generation.
    """
    import re as _re
    jenkins_url = "http://localhost:8080"
    user = "admin"
    script_dir = os.path.dirname(__file__)

    # 1. Check .jenkins_creds (preferred — written by requirements.sh)
    creds_file = os.path.join(script_dir, ".jenkins_creds")
    if os.path.exists(creds_file):
        with open(creds_file) as f:
            content = f.read()
        m = _re.search(r'JENKINS_TOKEN=([^\s]+)', content)
        if m and m.group(1).strip():
            return jenkins_url, user, m.group(1).strip()

    # 2. Fall back to admin:admin (set by Groovy init script)
    return jenkins_url, user, "admin"


def _jenkins_opener(auth_b64):
    """Return a urllib opener that maintains cookies (required for Jenkins CSRF crumb)."""
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    return opener


def _jenkins_wait_ready(jenkins_url, auth_b64, timeout=90):
    """Poll Jenkins /api/json until it responds or timeout expires. Returns True if ready."""
    opener = _jenkins_opener(auth_b64)
    headers = {"Authorization": f"Basic {auth_b64}"}
    for _ in range(timeout // 5):
        try:
            req = urllib.request.Request(f"{jenkins_url}/api/json", headers=headers)
            opener.open(req, timeout=5)
            return True
        except Exception:
            time.sleep(5)
    return False


def _jenkins_crumb(opener, jenkins_url, auth_b64):
    """Fetch crumb using the shared opener (preserves session cookie). Returns crumb header dict."""
    headers = {"Authorization": f"Basic {auth_b64}"}
    try:
        req = urllib.request.Request(f"{jenkins_url}/crumbIssuer/api/json", headers=headers)
        with opener.open(req, timeout=10) as r:
            d = json.loads(r.read())
        return {d["crumbRequestField"]: d["crumb"]}
    except urllib.error.HTTPError as e:
        if e.code == 404:  # crumb issuer disabled
            return {}
        raise


def provision_jenkins_token():
    """
    Wait for Jenkins to be ready, generate an API token using the initial admin
    password, and write it into run_nautobuff.sh automatically.
    Returns the token string, or empty string on failure.
    """
    import re as _re
    jenkins_url, user, cred = _jenkins_get_creds()
    run_nautobuff = os.path.join(os.path.dirname(__file__), "run_nautobuff.sh")

    # If we already have a real API token (not the plain password), skip generation
    if cred and cred != "admin":
        print("✅ Jenkins token already present — skipping generation")
        return cred

    auth_b64 = base64.b64encode(f"{user}:{cred}".encode()).decode()
    print("⏳ Waiting for Jenkins to be ready…")
    if not _jenkins_wait_ready(jenkins_url, auth_b64):
        print("⚠️  Jenkins did not become ready in time — skipping token generation")
        return ""

    opener = _jenkins_opener(auth_b64)
    base_headers = {"Authorization": f"Basic {auth_b64}", "Content-Type": "application/x-www-form-urlencoded"}
    try:
        crumb_headers = _jenkins_crumb(opener, jenkins_url, auth_b64)
    except Exception as e:
        print(f"⚠️  Could not fetch Jenkins crumb: {e}")
        return ""

    try:
        body = "newTokenName=NAutoBuff".encode()
        req = urllib.request.Request(
            f"{jenkins_url}/user/{user}/descriptorByName/jenkins.security.ApiTokenProperty/generateNewToken",
            data=body, headers={**base_headers, **crumb_headers}, method="POST",
        )
        with opener.open(req, timeout=10) as r:
            token = json.loads(r.read())["data"]["tokenValue"]
    except Exception as e:
        print(f"⚠️  Could not generate Jenkins API token: {e}")
        return ""

    creds_file = os.path.join(os.path.dirname(__file__), ".jenkins_creds")
    with open(creds_file, "w") as f:
        f.write(f"export JENKINS_USER=admin\n")
        f.write(f"export JENKINS_TOKEN={token}\n")
        f.write(f"export JENKINS_JOB_NAME=NAutoBuff\n")
    os.chmod(creds_file, 0o600)

    print("✅ Jenkins API token generated and saved to pilot-config/.jenkins_creds")
    return token


def provision_jenkins_plugins():
    """
    Ensure the required Jenkins plugins are installed.
    Skips gracefully if already installed or Jenkins is unreachable.
    """
    required = ["git", "workflow-job", "workflow-cps", "workflow-aggregator", "github", "pipeline-stage-view"]
    jenkins_url, user, cred = _jenkins_get_creds()
    if not cred:
        return

    auth_b64 = base64.b64encode(f"{user}:{cred}".encode()).decode()
    opener = _jenkins_opener(auth_b64)
    base_headers = {"Authorization": f"Basic {auth_b64}"}

    # Check which plugins are already active
    try:
        req = urllib.request.Request(
            f"{jenkins_url}/pluginManager/api/json?depth=1&tree=plugins[shortName,active]",
            headers=base_headers,
        )
        with opener.open(req, timeout=10) as r:
            data = json.loads(r.read())
        installed = {p["shortName"] for p in data.get("plugins", []) if p.get("active")}
    except Exception as e:
        print(f"⚠️  Could not query Jenkins plugins: {e} — skipping plugin check")
        return

    missing = [p for p in required if p not in installed]
    if not missing:
        print(f"✅ All required Jenkins plugins already installed")
        return

    print(f"⏳ Installing Jenkins plugins: {missing}")
    try:
        crumb_headers = _jenkins_crumb(opener, jenkins_url, auth_b64)
    except Exception as e:
        print(f"⚠️  Could not fetch crumb for plugin install: {e}")
        return

    xml = "<jenkins>" + "".join(
        f'<install plugin="{p}@latest"/>' for p in missing
    ) + "</jenkins>"

    try:
        req = urllib.request.Request(
            f"{jenkins_url}/pluginManager/installNecessaryPlugins",
            data=xml.encode(),
            headers={**base_headers, "Content-Type": "text/xml", **crumb_headers},
            method="POST",
        )
        opener.open(req, timeout=20)
        print(f"✅ Plugin install queued — Jenkins will restart automatically")
        time.sleep(30)
        _jenkins_wait_ready(jenkins_url, auth_b64, timeout=120)
        time.sleep(30)  # allow plugins to fully initialize after restart
        print(f"✅ Jenkins back up after plugin install")
    except Exception as e:
        print(f"⚠️  Plugin install request failed: {e}")


def provision_jenkins_job():
    """
    Create the NAutoBuff Pipeline job in Jenkins if it doesn't already exist.
    Detects the git remote URL automatically from the local repo.
    """
    jenkins_url, user, cred = _jenkins_get_creds()
    if not cred:
        print("⚠️  No Jenkins credentials available — skipping job creation")
        return

    # Restart Jenkins to ensure all plugin class loaders are fully initialized
    print("⏳ Restarting Jenkins to ensure plugins are fully loaded…")
    subprocess.run(["sudo", "systemctl", "restart", "jenkins"], check=True)
    auth_b64 = base64.b64encode(f"{user}:{cred}".encode()).decode()
    _jenkins_wait_ready(jenkins_url, auth_b64, timeout=120)
    time.sleep(10)  # extra settle time for class loaders

    opener = _jenkins_opener(auth_b64)
    base_headers = {"Authorization": f"Basic {auth_b64}"}
    job_name = "NAutoBuff"

    # Check if job already exists
    try:
        req = urllib.request.Request(f"{jenkins_url}/job/{job_name}/api/json", headers=base_headers)
        opener.open(req, timeout=10)
        print(f"✅ Jenkins job '{job_name}' already exists — skipping creation")
        return
    except urllib.error.HTTPError as e:
        if e.code != 404:
            print(f"⚠️  Unexpected Jenkins response checking job: {e.code} — skipping")
            return
    except Exception as e:
        print(f"⚠️  Could not reach Jenkins to check job: {e} — skipping")
        return

    # Detect git remote URL, convert SSH → HTTPS if needed
    base = find_base_path()
    try:
        r = subprocess.run(["git", "remote", "get-url", "origin"],
                           capture_output=True, text=True, check=True, cwd=str(base))
        remote = r.stdout.strip()
        # git@github.com:User/Repo.git → https://github.com/User/Repo.git
        if remote.startswith("git@"):
            remote = remote.replace(":", "/", 1).replace("git@", "https://", 1)
        if not remote.endswith(".git"):
            remote += ".git"
    except Exception:
        remote = "https://github.com/NetEngBuff/NAutoBuff.git"
        print(f"⚠️  Could not detect git remote — using default: {remote}")

    job_xml = f"""<?xml version='1.1' encoding='UTF-8'?>
<flow-definition plugin="workflow-job">
  <description>NAutoBuff CI/CD Pipeline — auto-created by pilot.py</description>
  <keepDependencies>false</keepDependencies>
  <properties>
    <com.coravy.hudson.plugins.github.GithubProjectProperty plugin="github">
      <projectUrl>{remote.replace('.git', '/')}</projectUrl>
    </com.coravy.hudson.plugins.github.GithubProjectProperty>
  </properties>
  <definition class="org.jenkinsci.plugins.workflow.cps.CpsScmFlowDefinition" plugin="workflow-cps">
    <scm class="hudson.plugins.git.GitSCM" plugin="git">
      <configVersion>2</configVersion>
      <userRemoteConfigs>
        <hudson.plugins.git.UserRemoteConfig>
          <url>{remote}</url>
        </hudson.plugins.git.UserRemoteConfig>
      </userRemoteConfigs>
      <branches>
        <hudson.plugins.git.BranchSpec>
          <name>*/main</name>
        </hudson.plugins.git.BranchSpec>
      </branches>
      <doGenerateSubmoduleConfigurations>false</doGenerateSubmoduleConfigurations>
      <submoduleCfg class="empty-list"/>
      <extensions/>
    </scm>
    <scriptPath>Jenkinsfile</scriptPath>
    <lightweight>true</lightweight>
  </definition>
  <triggers>
    <com.cloudbees.jenkins.GitHubPushTrigger plugin="github">
      <spec></spec>
    </com.cloudbees.jenkins.GitHubPushTrigger>
  </triggers>
  <disabled>false</disabled>
</flow-definition>"""

    try:
        crumb_headers = _jenkins_crumb(opener, jenkins_url, auth_b64)
    except Exception as e:
        print(f"⚠️  Could not fetch Jenkins crumb for job creation: {e}")
        return

    for attempt in range(1, 4):
        try:
            # Refresh opener and crumb each attempt (session may have expired)
            opener = _jenkins_opener(auth_b64)
            crumb_headers = _jenkins_crumb(opener, jenkins_url, auth_b64)
            body = job_xml.encode("utf-8")
            req = urllib.request.Request(
                f"{jenkins_url}/createItem?name={job_name}",
                data=body,
                headers={**base_headers, "Content-Type": "application/xml", **crumb_headers},
                method="POST",
            )
            with opener.open(req, timeout=15) as r:
                _ = r.read()
            print(f"✅ Jenkins job '{job_name}' created successfully (repo: {remote})")
            return
        except urllib.error.HTTPError as e:
            err_body = e.read().decode(errors="replace")[:200]
            if e.code == 500 and attempt < 3:
                print(f"⚠️  Jenkins returned 500 (plugins still loading) — retrying in 15s… (attempt {attempt}/3)")
                time.sleep(15)
            else:
                print(f"⚠️  Failed to create Jenkins job: HTTP {e.code} — {err_body}")
                return
        except Exception as e:
            print(f"⚠️  Failed to create Jenkins job: {e}")
            return


if __name__ == "__main__":
    provision_jenkins_firsttime()
    provision_jenkins_token()
    provision_jenkins_plugins()
    provision_jenkins_job()
    base = find_base_path()
    setup_monitoring(base)
    main()

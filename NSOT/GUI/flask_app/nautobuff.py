import os
import csv
import yaml
import shutil
import sys
import time
import docker
import json
import logging
import re
from logging.handlers import RotatingFileHandler
from datetime import datetime
from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    jsonify,
    stream_with_context,
    Response,
    flash,
    session,
)
from flask_login import (
    LoginManager,
    UserMixin,
    login_user,
    logout_user,
    login_required,
    current_user,
)
from flask_socketio import SocketIO, emit, disconnect as sio_disconnect
import paramiko
from functools import wraps
from jinja2 import Environment, FileSystemLoader
import subprocess
from threading import Thread
from pathlib import Path
import socket
import netifaces
from concurrent.futures import ThreadPoolExecutor, as_completed
from netmiko import ConnectHandler


# Get the current directory of this script
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, "..", ".."))

# Go up two levels and into the 'python-files' directory
python_files_dir = os.path.join(current_dir, "..", "..", "python-files")
predict_dir = os.path.join(current_dir, "..", "..", "machine_learning", "predict")
helper_dir = os.path.join(current_dir, "..", "..", "machine_learning", "helper")
templates_dir = os.path.join(current_dir, "..", "..", "templates")
pilot_config_dir = os.path.join(project_root, "pilot-config")
topology_dir = os.path.join(project_root, "NSOT", "topology")

# Add 'python-files' to the system path
sys.path.append(os.path.abspath(python_files_dir))
sys.path.append(os.path.abspath(helper_dir))
sys.path.append(os.path.abspath(predict_dir))
topo_path = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "../../../pilot-config/topo.yml")
)


def _is_topology_running():
    try:
        client = docker.from_env()
        return bool(client.containers.list(filters={"label": "containerlab"}))
    except Exception as e:
        print(f"[ℹ] Could not check containerlab state: {e}")
        return False


def _stop_containerlab_graph():
    try:
        subprocess.run(
            ["sudo", "pkill", "-f", "containerlab graph"],
            capture_output=True,
            text=True,
        )
    except Exception as e:
        print(f"[ℹ] Could not stop containerlab graph: {e}")


def _topology_page_context(**extra):
    yaml_generated = bool(extra.pop("yaml_generated", False))
    is_topology_running = _is_topology_running()
    has_topology_yaml = os.path.exists(topo_path)
    context = {
        "docker_images": get_docker_images(),
        "has_topology_yaml": has_topology_yaml,
        "yaml_generated": yaml_generated,
        "is_topology_running": is_topology_running,
        "can_view_yaml": has_topology_yaml and (yaml_generated or is_topology_running),
        "topology_yaml": "",
    }
    if context["has_topology_yaml"]:
        try:
            with open(topo_path, encoding="utf-8") as f:
                context["topology_yaml"] = f.read()
        except Exception as e:
            print(f"[ℹ] Could not read topology YAML: {e}")
    context.update(extra)
    return context


def _vendor_from_kind(kind):
    return {
        "ceos": "arista",
        "cisco_iol": "cisco",
        "crpd": "juniper",
        "vjunosswitch": "juniper",
        "linux": "linux",
    }.get((kind or "").strip().lower(), "")


# ── Application Logging Setup ────────────────────────────────────────────────
_LOG_DIR = os.path.join(project_root, "logs")
os.makedirs(_LOG_DIR, exist_ok=True)
_LOG_FILE = os.path.join(_LOG_DIR, "nautobuff.log")

# Routes whose access lines are just noise (polling / static assets)
_NOISY_PATHS = (
    "/clab-health",
    "/static/",
    "/api/services",
)


class _NoiseFilter(logging.Filter):
    def filter(self, record):
        msg = record.getMessage()
        return not any(p in msg for p in _NOISY_PATHS)


_file_handler = RotatingFileHandler(
    _LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
)
_file_handler.setLevel(logging.INFO)
_file_handler.setFormatter(logging.Formatter(
    "[%(asctime)s] %(levelname)-8s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
))
_file_handler.addFilter(_NoiseFilter())

# Attach to werkzeug so HTTP requests are logged to file (filtered)
_werkzeug_log = logging.getLogger("werkzeug")
_werkzeug_log.addHandler(_file_handler)
_werkzeug_log.addFilter(_NoiseFilter())


class _TeeStream:
    """Tee sys.stdout so every print() also lands in the log file with a timestamp."""
    def __init__(self, original):
        self._original = original
        self._log_file = open(_LOG_FILE, "a", encoding="utf-8", buffering=1)

    def write(self, msg):
        self._original.write(msg)
        stripped = msg.rstrip("\n")
        if stripped:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._log_file.write(f"[{ts}] APP      {stripped}\n")

    def flush(self):
        self._original.flush()
        self._log_file.flush()

    def __getattr__(self, name):
        return getattr(self._original, name)


sys.stdout = _TeeStream(sys.stdout)
# ─────────────────────────────────────────────────────────────────────────────

# Import your custom modules from 'python-files'
from create_hosts import write_hosts_csv
from user_db import (
    init_db, get_all_users, get_user_by_id, get_user_by_username,
    create_user, update_user_password, update_user_role, delete_user,
    verify_password, record_login, ROLES,
    create_invite, get_invite, consume_invite, get_all_invites, revoke_invite,
)
from ping import ping_local, ping_remote
from goldenConfig import generate_configs
from show_commands import execute_show_command, find_device_info
from generate_yaml import create_yaml_from_form_data, create_yaml_from_form_devices
from config_Gen import conf_gen
from update_topo import update_topology, get_hosts_from_csv
from dhcp_updates import configure_dhcp_relay, configure_dhcp_server
from update_hosts import update_hosts_csv, regenerate_hosts_csv
from git_jenkins import push_and_monitor_jenkins
from push_config import push_configuration
from push_uploaded_config import push_uploaded_config
from config_backup import rollback_to_golden_config
from read_IPAM import IPAMReader
from read_hosts import HostsReader
from clab_builder import build_clab_topology
from clab_push import get_docker_images
from ollama_utils import stop_ollama_model
from gnmi_hosts import update_gnmic_yaml_from_hosts

# File path for IPAM CSV file
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
IPAM_DIR = os.path.join(BASE_DIR, "..", "..", "IPAM")


def _restore_golden_configs(hostnames):
    """
    Push golden configs back to a list of devices after a topology redeploy.
    Skips any device that has no golden config file yet.
    """
    golden_dir = os.path.join(
        os.path.dirname(__file__), "..", "..", "golden_configs"
    )
    hosts_csv = os.path.join(IPAM_DIR, "hosts.csv")

    vendor_map = {}
    try:
        with open(hosts_csv, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                vendor_map[row["hostname"]] = (row.get("vendor") or "arista").strip().lower()
    except Exception:
        pass

    for hostname in hostnames:
        cfg_path = None
        for name in [f"{hostname}_golden.cfg", f"goldenconfigs_{hostname}.cfg"]:
            candidate = os.path.join(golden_dir, name)
            if os.path.exists(candidate):
                cfg_path = candidate
                break
        if not cfg_path:
            print(f"[INFO] No golden config for {hostname} — skipping restore")
            continue

        vendor = vendor_map.get(hostname, "arista")
        success, msg = rollback_to_golden_config(hostname, vendor)
        print(f"[{'✔' if success else '⚠'}] Golden config restore {hostname}: {msg}")


def _sync_hosts_clab_ips():
    """
    Keep the user-entered management IPs in hosts.csv intact.

    Containerlab also assigns Docker network addresses to nodes, but those are
    transport/runtime addresses. hosts.csv is the source of truth for the lab
    management IP configured on Ethernet1.100 and shown in Engineer Telemetry.
    """
    try:
        result = subprocess.run(
            ["sudo", "containerlab", "inspect", "--all", "--format", "json"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return
        data = json.loads(result.stdout or "{}")
        if isinstance(data, dict):
            containers = [c for lab in data.values()
                          for c in (lab if isinstance(lab, list) else [])]
        else:
            containers = data

        ip_map = {}
        for c in containers:
            name = c.get("name", "")
            ipv4 = c.get("ipv4_address", "")
            if not name or not ipv4:
                continue
            ip_map[name.rsplit("-", 1)[-1]] = ipv4.split("/")[0]

        if not ip_map:
            return

        print(f"[ℹ] Containerlab runtime IPs detected, hosts.csv preserved: {ip_map}")
    except Exception as e:
        print(f"[⚠] Could not inspect clab runtime IPs: {e}")


def _reset_hosts_csv_passwords(default_password="admin"):
    """
    After a topology redeploy the golden config resets all device passwords to
    the default.  Reset hosts.csv to match so gnmic and Netmiko can authenticate.
    """
    hosts_csv = os.path.join(IPAM_DIR, "hosts.csv")
    if not os.path.exists(hosts_csv):
        return
    try:
        rows, fieldnames = [], []
        with open(hosts_csv, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            fieldnames = list(reader.fieldnames or [])
            rows = list(reader)
        for row in rows:
            row["old_password"] = row.get("password", "")
            row["password"] = default_password
        with open(hosts_csv, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        print("[✔] hosts.csv passwords reset to default after topology deploy")
    except Exception as e:
        print(f"[⚠] Could not reset hosts.csv passwords: {e}")


def _apply_oob_netplan():
    """Apply eth1.100 OOB netplan after Containerlab creates host:eth1."""
    try:
        subprocess.run(["sudo", "netplan", "apply"], check=True, capture_output=True, text=True)
        print("[✔] OOB netplan applied")
    except subprocess.CalledProcessError as e:
        detail = e.stderr or e.stdout or str(e)
        print(f"[⚠] Could not apply OOB netplan: {detail}")


PILOT_DIR = os.path.join(BASE_DIR, "..", "..", "..", "pilot-config")
ipam_file_path = os.path.join(IPAM_DIR, "ipam_output.csv")

# Initialize
ipam_reader = IPAMReader(file_path=ipam_file_path, update_interval=10)
hosts_reader = HostsReader(BASE_DIR)

app = Flask(__name__)
app.secret_key = os.environ.get("NAUTOBUFF_SECRET_KEY", "nautobuff-dev-secret-change-in-prod")
socketio = SocketIO(app, async_mode="threading", cors_allowed_origins="*")

# ── SSH console session registry (keyed by Socket.IO sid) ─────────────────────
_ssh_sessions: dict = {}


def _cidr_prefix(value):
    value = str(value or "").strip()
    if not value or value.upper() == "N/A":
        return value or "N/A"
    if value.startswith("/"):
        value = value[1:]
    if value.isdigit():
        prefix = int(value)
        return f"/{prefix}" if 0 <= prefix <= 32 else value
    parts = value.split(".")
    if len(parts) != 4:
        return value
    try:
        octets = [int(part) for part in parts]
    except ValueError:
        return value
    if any(octet < 0 or octet > 255 for octet in octets):
        return value
    bits = "".join(f"{octet:08b}" for octet in octets)
    if "01" in bits:
        return value
    return f"/{bits.count('1')}"


def _cidr_value(value):
    prefix = _cidr_prefix(value)
    return prefix[1:] if prefix.startswith("/") else prefix


def _split_ip_prefix(ip_with_prefix, default_prefix="24"):
    value = (ip_with_prefix or "").strip()
    if not value:
        return "", ""
    if "/" not in value:
        return value, default_prefix
    ip, prefix = value.split("/", 1)
    prefix = prefix.strip().lstrip("/") or default_prefix
    return ip.strip(), prefix


@app.template_filter("cidr_prefix")
def cidr_prefix_filter(value):
    return _cidr_prefix(value)

# ── Flask-Login setup ──────────────────────────────────────────────────────────
login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Please log in to access this page."


class User(UserMixin):
    def __init__(self, data):
        self.id = str(data["id"])
        self.username = data["username"]
        self.role = data["role"]

    def is_admin(self):
        return self.role == "admin"

    def is_operator(self):
        return self.role in ("admin", "operator")


@login_manager.user_loader
def load_user(user_id):
    data = get_user_by_id(int(user_id))
    return User(data) if data else None


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin():
            return redirect(url_for("homepage"))
        return f(*args, **kwargs)
    return decorated


def operator_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_operator():
            return redirect(url_for("homepage"))
        return f(*args, **kwargs)
    return decorated


# Initialise user database on startup
init_db()

# Set up Jinja2 environment to load templates from 'NSOT/templates' folder
env = Environment(loader=FileSystemLoader(templates_dir))

def _get_vendor_for_device(device_id):
    """Look up vendor for a device from hosts.csv."""
    hosts_csv_path = os.path.join(IPAM_DIR, "hosts.csv")
    try:
        with open(hosts_csv_path, newline="") as f:
            for row in csv.DictReader(f):
                if row.get("hostname", "").strip() == device_id.strip():
                    return row.get("vendor", "").strip() or None
    except Exception:
        pass
    return None


TELEMETRY_COMMANDS = {
    "arista": {
        "l2": [
            ("Interfaces", "show interfaces status"),
            ("MAC Table", "show mac address-table"),
            ("LLDP", "show lldp neighbors"),
            ("VLANs", "show vlan brief"),
        ],
        "l3": [
            ("IP Interfaces", "show ip interface brief"),
            ("Routes", "show ip route"),
            ("BGP", "show ip bgp summary"),
            ("OSPF", "show ip ospf neighbor"),
        ],
        "device": [
            ("Version", "show version"),
            ("Clock", "show clock"),
            ("CPU", "show processes top once"),
            ("Counters", "show interfaces counters"),
        ],
    },
    "cisco": {
        "l2": [
            ("Interfaces", "show interfaces status"),
            ("MAC Table", "show mac address-table"),
            ("LLDP", "show lldp neighbors"),
            ("VLANs", "show vlan brief"),
        ],
        "l3": [
            ("IP Interfaces", "show ip interface brief"),
            ("Routes", "show ip route"),
            ("BGP", "show ip bgp summary"),
            ("OSPF", "show ip ospf neighbor"),
        ],
        "device": [
            ("Version", "show version"),
            ("Clock", "show clock"),
            ("CPU", "show processes cpu sorted"),
            ("Counters", "show interfaces counters"),
        ],
    },
    "juniper": {
        "l2": [
            ("Interfaces", "show interfaces terse"),
            ("MAC Table", "show ethernet-switching table"),
            ("LLDP", "show lldp neighbors"),
            ("VLANs", "show vlans"),
        ],
        "l3": [
            ("IP Interfaces", "show interfaces terse"),
            ("Routes", "show route summary"),
            ("BGP", "show bgp summary"),
            ("OSPF", "show ospf neighbor"),
        ],
        "device": [
            ("Version", "show version"),
            ("Uptime", "show system uptime"),
            ("Hardware", "show chassis hardware"),
            ("Alarms", "show chassis alarms"),
        ],
    },
}


def _read_hosts_inventory():
    hosts_csv_path = os.path.join(IPAM_DIR, "hosts.csv")
    hosts = []
    try:
        with open(hosts_csv_path, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                hostname = (row.get("hostname") or "").strip()
                if not hostname:
                    continue
                hosts.append({
                    "hostname": hostname,
                    "management_ip": (row.get("management_ip") or "").strip(),
                    "vendor": (row.get("vendor") or "arista").strip().lower(),
                })
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"Error reading telemetry hosts inventory: {e}")
    return hosts


def _commands_for_vendor(vendor):
    return TELEMETRY_COMMANDS.get((vendor or "").lower(), TELEMETRY_COMMANDS["arista"])


def _run_device_telemetry(hostname, groups):
    device_info = find_device_info(hostname)
    if not device_info:
        return {
            "success": False,
            "device": hostname,
            "error": f"Device {hostname} not found in hosts.csv",
            "groups": {},
        }

    vendor = (_get_vendor_for_device(hostname) or "arista").strip().lower()
    command_map = _commands_for_vendor(vendor)
    selected_groups = [g for g in groups if g in command_map] or ["l2", "l3", "device"]
    output_groups = {}

    try:
        ssh_conn = ConnectHandler(**device_info)
        if device_info.get("device_type") != "juniper_junos":
            ssh_conn.enable()
        for group in selected_groups:
            output_groups[group] = []
            for label, command in command_map[group]:
                try:
                    output = ssh_conn.send_command(command, read_timeout=20)
                    output_groups[group].append({
                        "label": label,
                        "command": command,
                        "success": True,
                        "output": output,
                    })
                except Exception as cmd_error:
                    output_groups[group].append({
                        "label": label,
                        "command": command,
                        "success": False,
                        "output": str(cmd_error),
                    })
        ssh_conn.disconnect()
        return {
            "success": True,
            "device": hostname,
            "vendor": vendor,
            "groups": output_groups,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    except Exception as e:
        return {
            "success": False,
            "device": hostname,
            "vendor": vendor,
            "error": str(e),
            "groups": output_groups,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }


def _run_custom_telemetry(hostname, commands, group_id="custom"):
    device_info = find_device_info(hostname)
    if not device_info:
        return {
            "success": False,
            "device": hostname,
            "error": f"Device {hostname} not found in hosts.csv",
            "groups": {},
        }

    clean_commands = []
    for item in commands[:12]:
        command = (item.get("command") or "").strip()
        label = (item.get("label") or command).strip()
        if not command or not command.lower().startswith("show "):
            continue
        clean_commands.append((label[:60], command[:180]))

    if not clean_commands:
        return {
            "success": False,
            "device": hostname,
            "error": "At least one show command is required.",
            "groups": {},
        }

    vendor = (_get_vendor_for_device(hostname) or "arista").strip().lower()
    output_groups = {group_id: []}

    try:
        ssh_conn = ConnectHandler(**device_info)
        if device_info.get("device_type") != "juniper_junos":
            ssh_conn.enable()
        for label, command in clean_commands:
            try:
                output = ssh_conn.send_command(command, read_timeout=20)
                output_groups[group_id].append({
                    "label": label,
                    "command": command,
                    "success": True,
                    "output": output,
                })
            except Exception as cmd_error:
                output_groups[group_id].append({
                    "label": label,
                    "command": command,
                    "success": False,
                    "output": str(cmd_error),
                })
        ssh_conn.disconnect()
        return {
            "success": True,
            "device": hostname,
            "vendor": vendor,
            "groups": output_groups,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    except Exception as e:
        return {
            "success": False,
            "device": hostname,
            "vendor": vendor,
            "error": str(e),
            "groups": output_groups,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }


# Context processor to make devices + current time available in all templates
@app.context_processor
def inject_globals():
    try:
        devices = hosts_reader.get_devices()
    except Exception as e:
        print(f"Error loading devices for context: {e}")
        devices = []
    return dict(
        devices=devices,
        now=time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
    )


# ── Auth routes ───────────────────────────────────────────────────────────────
@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("homepage"))
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user_data = verify_password(username, password)
        if user_data:
            user = User(user_data)
            login_user(user, remember=request.form.get("remember") == "on")
            record_login(user_data["id"])
            next_page = request.args.get("next", "")
            if next_page and next_page.startswith("/") and not next_page.startswith("//"):
                return redirect(next_page)
            return redirect(url_for("homepage"))
        error = "Invalid username or password."
    return render_template("login.html", error=error)


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


# ── Invite signup ─────────────────────────────────────────────────────────────
@app.route("/signup/<token>", methods=["GET", "POST"])
def signup(token):
    invite = get_invite(token)
    if not invite:
        return render_template("signup.html", error="This invite link is invalid or has expired.", invite=None)

    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm  = request.form.get("confirm_password", "")

        if not username or not password:
            error = "Username and password are required."
        elif password != confirm:
            error = "Passwords do not match."
        elif len(password) < 8:
            error = "Password must be at least 8 characters."
        elif get_user_by_username(username):
            error = f"Username '{username}' is already taken."
        else:
            create_user(username, password, invite["role"])
            consume_invite(token)
            return render_template("signup.html", success=True, invite=invite)

    return render_template("signup.html", invite=invite, error=error)



# ── Admin: user management ─────────────────────────────────────────────────────
@app.route("/admin/users", methods=["GET", "POST"])
@login_required
@admin_required
def admin_users():
    message = None
    error = None
    invite_url = None
    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "create":
                uname = request.form.get("username", "").strip()
                pwd = request.form.get("password", "")
                role = request.form.get("role", "viewer")
                if not uname or not pwd:
                    error = "Username and password are required."
                elif get_user_by_username(uname):
                    error = f"Username '{uname}' already exists."
                else:
                    create_user(uname, pwd, role)
                    message = f"User '{uname}' created as {role}."
            elif action == "delete":
                uid = int(request.form.get("user_id"))
                target = get_user_by_id(uid)
                if target and target["username"] == current_user.username:
                    error = "You cannot delete your own account."
                elif target:
                    delete_user(uid)
                    message = f"User '{target['username']}' deleted."
            elif action == "change_role":
                uid = int(request.form.get("user_id"))
                role = request.form.get("role")
                target = get_user_by_id(uid)
                if target and target["username"] == current_user.username:
                    error = "You cannot change your own role."
                elif target:
                    update_user_role(uid, role)
                    message = f"Role updated for '{target['username']}'."
            elif action == "reset_password":
                uid = int(request.form.get("user_id"))
                pwd = request.form.get("new_password", "")
                if not pwd:
                    error = "New password cannot be empty."
                else:
                    update_user_password(uid, pwd)
                    message = "Password updated."
            elif action == "invite":
                role = request.form.get("role", "viewer")
                token = create_invite(role, current_user.username)
                invite_url = url_for("signup", token=token, _external=True)
                message = f"Invite link generated for role: {role}."
            elif action == "revoke_invite":
                revoke_invite(request.form.get("token"))
                message = "Invite revoked."
        except Exception as e:
            error = str(e)
    users = get_all_users()
    invites = get_all_invites()
    return render_template("admin_users.html", users=users, invites=invites, roles=ROLES,
                           message=message, error=error, invite_url=invite_url)


# ── Admin: hosts inventory ─────────────────────────────────────────────────────
@app.route("/admin/hosts-inventory")
@login_required
@admin_required
def hosts_inventory():
    hosts = []
    hosts_csv_path = os.path.join(IPAM_DIR, "hosts.csv")
    try:
        with open(hosts_csv_path, newline="", encoding="utf-8-sig") as f:
            hosts = list(csv.DictReader(f))
    except Exception as e:
        print(f"[⚠] Could not read hosts.csv: {e}")
    return render_template("hosts_inventory.html", hosts=hosts)


@app.route("/admin/hosts-inventory/delete", methods=["POST"])
@login_required
@admin_required
def delete_host():
    hostname = request.form.get("hostname", "").strip()
    if not hostname:
        return ("Missing hostname", 400)
    hosts_csv_path = os.path.join(IPAM_DIR, "hosts.csv")
    try:
        with open(hosts_csv_path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames or []
            rows = [r for r in reader if r.get("hostname", "").strip() != hostname]
        with open(hosts_csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        print(f"[✔] Deleted host '{hostname}' from hosts.csv")
    except Exception as e:
        print(f"[⚠] Failed to delete host '{hostname}': {e}")
        return (f"Error: {e}", 500)
    return redirect(url_for("hosts_inventory"))


@app.route("/")
@login_required
def homepage():
    devices = hosts_reader.get_devices()
    return render_template("homepage.html", devices=devices)


def _llm_params_to_pipeline(device, template, params):
    """
    Adapter: converts LLM-extracted template + params into kwargs for
    create_yaml_from_form_data() so the chatbot reuses the same pipeline
    as the web UI (conf_gen → git → Jenkins).

    Returns (kwargs_dict, None) on success.
    Returns (None, "dhcp_direct") for DHCP (not supported by build_device_data).
    Returns (None, error_str) on failure.
    """
    import re as _re

    # Look up vendor from hosts.csv
    csv_path = os.path.join(current_dir, "..", "..", "IPAM", "hosts.csv")
    vendor = "arista"
    try:
        with open(csv_path, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                if row["hostname"].strip() == device:
                    vendor = row.get("vendor", "arista").strip().lower()
                    break
    except Exception:
        pass

    kwargs = {"device_id": device, "device_vendor": vendor, "interfaces": []}

    if template == "vlan_template.j2":
        kwargs["vlans"] = params.get("vlans", [])

    elif template == "interfaces_template.j2":
        mapped = []
        for iface in params.get("interfaces", []):
            name = iface.get("name", "")
            m = _re.match(r"([A-Za-z]+)(\d+.*)", name)
            if m:
                mapped.append({
                    "type": m.group(1),
                    "number": m.group(2),
                    "ip": iface.get("ip_address"),
                    "mask": iface.get("subnet_mask"),
                })
        kwargs["interfaces"] = mapped

    elif template == "bgp_template.j2":
        bgp = params.get("bgp", {})
        for af in bgp.get("address_families", []):
            af.setdefault("type", "ipv4")
        kwargs["bgp"] = bgp

    elif template == "ospf_template.j2":
        redistribute = params.get("ospf_redistribute") or {}
        kwargs["ospf"] = {
            "process_id": params.get("ospf_process"),
            "networks": params.get("ospf_networks", []),
            "redistribute_connected": redistribute.get("connected", False),
            "redistribute_bgp": redistribute.get("bgp", False),
        }

    elif template == "rip_template.j2":
        kwargs["rip"] = {
            "version": params.get("rip_version", 2),
            "networks": [{"ip": n} for n in params.get("rip_networks", [])],
            "redistribute": {
                "bgp": params.get("bgp_redistribute", False),
                "metric": params.get("bgp_metric") or 1,
            },
        }

    elif template == "subinterface_template.j2":
        kwargs["subinterfaces"] = params.get("subinterfaces", [])

    elif template == "dhcp_template.j2":
        return None, "dhcp_direct"

    else:
        return None, f"No pipeline mapping for template '{template}'"

    return kwargs, None


def _ollama_available():
    """Return True if the Ollama daemon is reachable on localhost."""
    try:
        import urllib.request as _ur
        _ur.urlopen("http://localhost:11434", timeout=2)
        return True
    except Exception:
        return False


def _get_installed_models():
    """Return list of installed Ollama model names, empty list on failure."""
    try:
        import urllib.request as _ur
        with _ur.urlopen("http://localhost:11434/api/tags", timeout=3) as r:
            data = json.loads(r.read())
        return [m["name"] for m in data.get("models", [])]
    except Exception:
        return []


@app.route("/api/ollama/models")
@login_required
def get_ollama_models():
    """List all models currently installed in Ollama."""
    models = _get_installed_models()
    # Use session model if set and still installed, otherwise first available
    selected = session.get("ollama_model")
    if not selected or selected not in models:
        selected = models[0] if models else None
    return jsonify({"models": models, "selected": selected})


@app.route("/api/ollama/model", methods=["POST"])
@login_required
def set_ollama_model():
    """Save the user's chosen model to their session."""
    model = (request.json or {}).get("model", "").strip()
    if model:
        session["ollama_model"] = model
    return jsonify({"model": session.get("ollama_model")})


@app.route("/chat-query", methods=["POST"])
@login_required
def chat_query():
    if not shutil.which("ollama"):
        return Response(
            stream_with_context(iter(["⚠️ AI chatbot is not installed. Run requirements.sh and choose to enable NBot."])),
            content_type="text/event-stream",
        )

    if not _ollama_available():
        return Response(
            stream_with_context(iter(["⚠️ Ollama is not running. Start it with: sudo systemctl start ollama"])),
            content_type="text/event-stream",
        )

    data = request.json
    user_input = (data.get("message") or "").strip()
    history = data.get("history") or []
    model = session.get("ollama_model")
    if not model:
        installed = _get_installed_models()
        model = installed[0] if installed else None
    if not model:
        return Response(
            stream_with_context(iter(["⚠️ No Ollama models installed. Run: ollama pull llama3.1:8b"])),
            content_type="text/event-stream",
        )

    def generate():
        from llm_extract import extract_and_plan, interpret_output
        from fetch_show import connect_and_run_command
        from generate_config import render_device_config

        if not user_input:
            yield "⚠️ Please enter a message."
            return

        actions = extract_and_plan(user_input, history=history, model=model)
        if not actions:
            yield "⚠️ Sorry, I could not understand that request."
            return

        multi = len([a for a in actions if a.get("type") != "smalltalk"]) > 1

        for action in actions:
            action_type = action.get("type")
            device = action.get("device")

            if action_type == "smalltalk":
                yield action.get("response") or "👋 Hi! How can I help with your network today?"

            elif action_type == "monitor":
                cli_command = action.get("cli_command")
                if not cli_command or not device:
                    yield "⚠️ Missing command or device for this action."
                    continue

                if multi:
                    yield f"\n{device}:\n"

                cli_output = connect_and_run_command(device, cli_command)
                if cli_output:
                    # Scope the question to this device only so the LLM
                    # doesn't try to answer for all devices with partial data
                    device_question = f"Running '{cli_command}' on {device} — what does the output show?"
                    answer = interpret_output(device_question, cli_output, model=model)
                    yield answer or "⚠️ Could not interpret device output."
                else:
                    yield f"⚠️ No output from {device}. Check the device is reachable."

            elif action_type == "configure":
                template = action.get("template")
                params = action.get("params") or {}

                if not template or not device:
                    yield "⚠️ Missing template or device for configuration."
                    continue

                yield f"🔧 Generating config for {device}...\n"

                kwargs, err = _llm_params_to_pipeline(device, template, params)

                if err == "dhcp_direct":
                    # DHCP not wired into build_device_data — render directly
                    config_text = render_device_config(device, template, params)
                    if config_text:
                        yield f"✅ DHCP config generated for {device}:\n\n```\n{config_text}\n```"
                    else:
                        yield f"⚠️ Failed to generate DHCP configuration."
                    continue

                if kwargs is None:
                    yield f"⚠️ {err}"
                    continue

                try:
                    create_yaml_from_form_data(**kwargs)
                    conf_gen()
                    yield f"📤 Pushing to Git and triggering Jenkins...\n"
                    result = push_and_monitor_jenkins()
                    if result == "SUCCESS":
                        yield f"✅ Jenkins passed — pushing config to {device}...\n"
                        push_result = push_configuration(device)
                        if "successfully" in push_result:
                            yield f"✅ Config deployed to {device}!"
                        else:
                            yield f"⚠️ Jenkins passed but device push failed: {push_result}"
                    else:
                        yield f"❌ Jenkins pipeline returned: {result}. Check Jenkins for details."
                except Exception as e:
                    yield f"⚠️ Pipeline error: {e}"

            else:
                yield f"⚠️ Unknown action type: '{action_type}'"

    return Response(
        stream_with_context(generate()), content_type="text/event-stream"
    )


@app.route("/shutdown-ollama", methods=["POST"])
@login_required
@operator_required
def shutdown_ollama_route():
    try:
        stop_ollama_model("llama3.2:3b")
        return jsonify({"status": "success", "message": "Ollama stopped."})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})


@app.route("/add-hosts", methods=["GET", "POST"])
@login_required
@admin_required
def add_hosts():
    message = None
    if request.method == "POST":
        hostnames = request.form.getlist("hostname[]")
        usernames = request.form.getlist("username[]")
        passwords = request.form.getlist("password[]")
        management_ips = request.form.getlist("management_ip[]")
        subnet_cidrs = request.form.getlist("subnet_cidr[]")
        vendors = request.form.getlist("vendor[]")
        rows = []
        for i in range(len(hostnames)):
            if hostnames[i] and usernames[i] and passwords[i] and management_ips[i] and subnet_cidrs[i]:
                subnet_cidr = _cidr_value(subnet_cidrs[i])
                rows.append(
                    [hostnames[i], usernames[i], passwords[i], management_ips[i], subnet_cidr, vendors[i] if i < len(vendors) else ""]
                )

        if rows:
            path = write_hosts_csv(rows, append=True)
            flash(f"Saved {len(rows)} host(s) to hosts.csv.")
            return redirect(url_for("hosts_inventory"))
        else:
            message = "⚠️ No valid entries to save."
            

    return render_template("add_hosts.html", message=message)


@app.route("/build-topology", methods=["GET", "POST"])
@login_required
@operator_required
def build_topology():
    if request.method == "POST" and "generate" in request.form:
        topo_name = request.form.get("topo_name", "custom_topo")
        devices = []
        links = []

        # ✅ Collect device entries
        i = 0
        while True:
            name = request.form.get(f"device_name_{i}")
            if not name:
                break

            kind = request.form.get(f"device_kind_{i}")
            image = request.form.get(f"device_image_{i}")
            config = request.form.get(f"device_config_{i}")
            exec_lines = request.form.getlist(f"device_exec_{i}[]")
            ip_with_subnet = request.form.get(f"device_mgmt_ip_{i}", "")
            ip_address, subnet_cidr = _split_ip_prefix(ip_with_subnet)
            username = request.form.get(f"device_username_{i}", "")
            password = request.form.get(f"device_password_{i}", "")
            vendor = (
                request.form.get(f"device_vendor_{i}", "").strip().lower()
                or (_get_vendor_for_device(name) or "").strip().lower()
                or _vendor_from_kind(kind)
            )

            devices.append(
                {
                    "name": name,
                    "kind": kind,
                    "image": image,
                    "config": config,
                    "exec": exec_lines,
                    "mgmt_ip": ip_with_subnet,
                    "ip_address": ip_address,
                    "subnet_cidr": subnet_cidr,
                    "username": username,
                    "password": password,
                    "vendor": vendor,
                }
            )

            i += 1

        device_names = {device["name"] for device in devices}

        # ✅ Parse every rendered link select field. Do not rely on link_count
        # or hidden JSON; both can be stale if the dynamic form is re-rendered.
        link_indexes = sorted(
            {
                int(match.group(1))
                for key in request.form.keys()
                for match in [re.fullmatch(r"link_dev[12]_(\d+)", key)]
                if match
            }
        )
        for link_index in link_indexes:
            links.append((
                request.form.get(f"link_dev1_{link_index}", ""),
                request.form.get(f"link_dev2_{link_index}", ""),
            ))

        link_dev1_list = request.form.get("link_dev1_json")
        link_dev2_list = request.form.get("link_dev2_json")
        if not links and link_dev1_list and link_dev2_list:
            try:
                dev1_list = json.loads(link_dev1_list)
                dev2_list = json.loads(link_dev2_list)
            except json.JSONDecodeError as e:
                return render_template(
                    "build_topology.html",
                    **_topology_page_context(error=f"Invalid link data: {e}")
                )
            links = list(zip(dev1_list, dev2_list))

        links = [
            (dev1, dev2)
            for dev1, dev2 in links
            if dev1 and dev2 and dev1 != dev2 and dev1 in device_names and dev2 in device_names
        ]
        print(f"[INFO] Parsed topology links from form: {links}")

        # ✅ Build topology and update CSV
        print("[INFO] Generating topology YAML...")
        output_path = build_clab_topology(topo_name, devices, links)
        print(f"[✔] YAML saved at: {output_path}")

        print("[INFO] Generating hosts.csv...")
        regenerate_hosts_csv(devices)

        session["topology_yaml_generated"] = True
        message = f"✅ topo.yml generated at: <code>{output_path}</code>"

        return render_template(
            "build_topology.html", **_topology_page_context(message=message, yaml_generated=True)
        )

    # GET fallback
    return render_template("build_topology.html", **_topology_page_context())


@app.route("/deploy-topology", methods=["POST"], endpoint="deploy_topology_route")
@login_required
@operator_required
def deploy_topology_route():
    yaml_path = os.path.abspath(
        os.path.join(
            os.path.dirname(__file__), "..", "..", "..", "pilot-config", "topo.yml"
        )
    )
    
    print("[INFO] Destroying old topology...")
    subprocess.run(["sudo", "containerlab", "destroy", "-t", yaml_path],
                   capture_output=True, text=True)

    # Cleanup directory
    try:
        with open(yaml_path) as f:
            data = yaml.safe_load(f) or {}
        lab_name = data.get("name", "unknown")
        lab_dir = Path(yaml_path).parent / f"clab-{lab_name}"
        if lab_dir.exists() and lab_dir.is_dir():
            shutil.rmtree(lab_dir)
            print(f"[✔] Deleted old lab folder: {lab_dir}")
    except Exception as e:
        print(f"[ℹ] Lab folder cleanup skipped: {e}")

    print("[INFO] Deploying new topology...")
    try:
        # We capture output to prevent the "messy" terminal logs you saw earlier
        result = subprocess.run(
            ["sudo", "containerlab", "deploy", "-t", yaml_path],
            capture_output=True,
            text=True,
            check=True
        )

        deploy_output = result.stdout
        time.sleep(2)
        _apply_oob_netplan()
        _reset_hosts_csv_passwords()
        _sync_hosts_clab_ips()
        update_gnmic_yaml_from_hosts()

        # System services still require sudo, ensure NOPASSWD is set in sudoers
        subprocess.run(["sudo", "systemctl", "restart", "gnmic_nautobuff.service"], check=True)
        subprocess.run(["sudo", "systemctl", "restart", "ipam.service"], check=True)

        print("[✔] Deploy output captured successfully.")
        session["topology_yaml_generated"] = False
        message = "✅ Containerlab topology deployed successfully."

    except subprocess.CalledProcessError as e:
        # e.stderr or e.stdout contains the reason for failure (like "requires root privileges")
        error_detail = e.stderr if e.stderr else e.stdout
        print(f"[ERROR] Deployment failed: {error_detail}")
        message = f"❌ Failed to deploy topology:<br><pre>{error_detail}</pre>"

    return render_template(
        "build_topology.html", **_topology_page_context(message=message)
    )


@app.route("/delete-topology", methods=["POST"], endpoint="delete_topology_route")
@login_required
@operator_required
def delete_topology_route():
    yaml_path = os.path.abspath(
        os.path.join(
            os.path.dirname(__file__), "..", "..", "..", "pilot-config", "topo.yml"
        )
    )
    print("[INFO] Deleting topology...")
    _stop_containerlab_graph()

    # If topo.yml is missing, try to find the lab name from clab-*/topology-data.json
    # and destroy by name — handles cases where the file was moved or gitignored away
    destroy_cmd = ["sudo", "containerlab", "destroy", "-t", yaml_path]
    if not os.path.exists(yaml_path):
        clab_dirs = sorted(Path(yaml_path).parent.glob("clab-*/topology-data.json"))
        lab_name = None
        for td in clab_dirs:
            try:
                with open(td) as f:
                    lab_name = json.load(f).get("name")
                if lab_name:
                    break
            except Exception:
                pass
        if lab_name:
            destroy_cmd = ["sudo", "containerlab", "destroy", "--name", lab_name, "--cleanup"]
        else:
            return render_template(
                "build_topology.html",
                **_topology_page_context(message="⚠️ No topology is currently deployed.")
            )

    try:
        result = subprocess.run(
            destroy_cmd,
            capture_output=True,
            text=True,
            check=True
        )

        # Clean up clab-* folder if it still exists
        if os.path.exists(yaml_path):
            with open(yaml_path) as f:
                data = yaml.safe_load(f)
            lab_name = data.get("name", "unknown")
        # lab_name may already be set from the --name fallback path above
        if lab_name:
            lab_dir = Path(yaml_path).parent / f"clab-{lab_name}"
            if lab_dir.exists() and lab_dir.is_dir():
                shutil.rmtree(lab_dir)

        message = "✅ Topology deleted successfully."
        session["topology_yaml_generated"] = False
        _stop_containerlab_graph()
        print("[✔] Topology destroyed and folder cleaned.")

    except subprocess.CalledProcessError as e:
        error_detail = e.stderr if e.stderr else e.stdout
        print(f"[ERROR] Delete failed: {error_detail}")
        message = f"❌ Failed to delete topology:<br><pre>{error_detail}</pre>"

    return render_template(
        "build_topology.html", **_topology_page_context(message=message)
    )


@app.route("/add-device", methods=["GET", "POST"])
@login_required
@operator_required
def add_device():
    message = None

    if request.method == "POST":
        device_name = request.form["device_name"]
        kind = request.form["kind"]
        image = request.form["image"]
        config = request.form.get("config", "")
        exec_lines = request.form.getlist("exec[]")
        mac_address = request.form.get("mac_address", "")
        ip_with_subnet = request.form.get("ip_address", "")
        ip_address = ip_with_subnet.split("/")[0]
        subnet_cidr = ip_with_subnet.split("/")[1] if "/" in ip_with_subnet else ""
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        vendor = request.form.get("vendor", "arista")
        try:
            connection_count = max(0, min(int(request.form.get("connection_count", "0")), 50))
        except (ValueError, TypeError):
            connection_count = 0

        # Optional DHCP relay values
        relay_toggle = request.form.get("relay_toggle")
        connected_ip = request.form.get("connected_ip")
        helper_ip = request.form.get("helper_ip")
        dhcp_server = request.form.get("dhcp_server")
        new_subnet = request.form.get("new_subnet")
        range_lower = request.form.get("range_lower")
        range_upper = request.form.get("range_upper")
        default_gateway = request.form.get("default_gateway")

        connect_to = [
            request.form.get(f"connect_to_{i}")
            for i in range(connection_count)
            if request.form.get(f"connect_to_{i}")
        ]

        topo_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "../../../pilot-config/topo.yml")
        )
        # Capture existing devices before we add the new one, so we can
        # restore their golden configs after the topology redeploy.
        existing_devices = get_hosts_from_csv()

        print("[INFO] Destroying old topology before update...")
        subprocess.run(["sudo", "containerlab", "destroy", "-t", topo_path],
                       capture_output=True, text=True)

        try:
            update_topology(
                topo_path=topo_path,
                device_name=device_name,
                kind=kind,
                image=image,
                config=config,
                exec_lines=exec_lines,
                mac=mac_address,
                connect_to_list=connect_to,
                mgmt_ip=ip_with_subnet,
                username=username,
                password=password,
            )

            update_hosts_csv(
                device_name, ip_address,
                username=username, password=password,
                subnet_cidr=subnet_cidr, vendor=vendor,
            )

            print("[INFO] Deploying new topology...")
            deploy_output = subprocess.check_output(
                ["sudo", "containerlab", "deploy", "-t", topo_path],
                stderr=subprocess.STDOUT,
                text=True,
            )
            time.sleep(2)
            _apply_oob_netplan()
            _reset_hosts_csv_passwords()
            _sync_hosts_clab_ips()
            _restore_golden_configs(existing_devices)
            update_gnmic_yaml_from_hosts()
            subprocess.run(
                ["sudo", "systemctl", "restart", "gnmic_nautobuff.service"], check=True
            )
            subprocess.run(["sudo", "systemctl", "restart", "ipam.service"], check=True)
            print("[✔] Deploy output:")
            print(deploy_output)

            if relay_toggle:
                configure_dhcp_relay(
                    connected_device=connect_to[0] if connect_to else "",
                    connected_interface="eth1",
                    connected_ip=connected_ip,
                    helper_ip=helper_ip,
                )
                configure_dhcp_server(
                    mac_address,
                    dhcp_server,
                    new_subnet,
                    range_lower,
                    range_upper,
                    default_gateway,
                    ip_address,
                )

            message = "✅ Topology deployed successfully."

        except subprocess.CalledProcessError as e:
            print("[ERROR] Deployment failed:")
            print(e.output)
            message = f"❌ Deployment failed:<br><pre>{e.output}</pre>"

    docker_images = [
        tag for img in docker.from_env().images.list() for tag in img.tags if ":" in tag
    ]
    return render_template(
        "add_device.html",
        docker_images=docker_images,
        available_hosts=get_hosts_from_csv(),
        message=message,
    )


@app.route("/configure-device", methods=["GET", "POST"])
@login_required
@operator_required
def configure_device():
    try:
        devices = hosts_reader.get_devices()
        if request.method == "POST":
            submitted_form = request.form.to_dict(flat=False)
            device_ids = [
                device_id.strip()
                for device_id in request.form.getlist("device_id")
                if device_id.strip()
            ]
            device_id = device_ids[0] if device_ids else None
            missing_vendor_devices = [
                target for target in device_ids if not _get_vendor_for_device(target)
            ]
            if not device_ids:
                return render_template(
                    "configure_device.html",
                    devices=devices,
                    device_ids=device_ids,
                    device_id=device_id,
                    submitted_form=submitted_form,
                    jenkins_result="jenkins_failure",
                    message="⚠️ Select at least one device.",
                )
            if missing_vendor_devices:
                return render_template(
                    "configure_device.html",
                    devices=devices,
                    device_ids=device_ids,
                    device_id=device_id,
                    submitted_form=submitted_form,
                    jenkins_result="jenkins_failure",
                    message=f"⚠️ No vendor set for {', '.join(missing_vendor_devices)}. Please update Hosts Inventory.",
                )

            # Interfaces
            interfaces = []
            for i_type, i_num, ip, mask, sp in zip(
                request.form.getlist("interface_type[]"),
                request.form.getlist("interface_number[]"),
                request.form.getlist("interface_ip[]"),
                request.form.getlist("interface_mask[]"),
                request.form.getlist("switchport[]"),
            ):
                interfaces.append(
                    {
                        "type": i_type,
                        "number": i_num,
                        "ip": ip if sp != "yes" else None,
                        "mask": _cidr_value(mask) if sp != "yes" else None,
                        "switchport": sp == "yes",
                    }
                )

            # Subinterfaces
            subinterfaces = []
            for parent, sid, vlan, ip, mask in zip(
                request.form.getlist("subinterface_parent[]"),
                request.form.getlist("subinterface_id[]"),
                request.form.getlist("subinterface_vlan[]"),
                request.form.getlist("subinterface_ip[]"),
                request.form.getlist("subinterface_mask[]"),
            ):
                subinterfaces.append(
                    {"parent": parent, "id": sid, "vlan": vlan, "ip": ip, "mask": _cidr_value(mask)}
                )

            # VLANs
            vlans = []
            for vlan_id, vlan_name in zip(
                request.form.getlist("vlan_id[]"),
                request.form.getlist("vlan_name[]"),
            ):
                vlans.append({"id": vlan_id, "name": vlan_name})

            # RIP
            rip = None
            rip_versions = request.form.getlist("rip_version[]")
            rip_networks = request.form.getlist("rip_network[]")
            rip_redistribute_selected = request.form.get("rip_redistribute")
            rip_bgp_as = request.form.getlist("rip_bgp_as[]")
            rip_bgp_metric = request.form.getlist("rip_bgp_metric[]")

            if rip_versions:
                rip = {
                    "version": rip_versions[0],
                    "networks": [{"ip": net} for net in rip_networks if net],
                }

                if rip_redistribute_selected:
                    redistribute = {}
                    if rip_bgp_as and rip_bgp_as[0]:
                        redistribute["as_number"] = rip_bgp_as[0]
                    if rip_bgp_metric and rip_bgp_metric[0]:
                        redistribute["metric"] = int(rip_bgp_metric[0])
                    if redistribute:
                        rip["redistribute"] = redistribute

            # OSPF
            ospf = None
            ospf_process_ids = request.form.getlist("ospf_process_id[]")
            ospf_networks = request.form.getlist("ospf_network[]")
            ospf_wildcards = request.form.getlist("ospf_wildcard[]")
            ospf_areas = request.form.getlist("ospf_area[]")
            ospf_redistribute_connected = request.form.getlist(
                "ospf_redistribute_connected[]"
            )
            ospf_redistribute_bgp = request.form.getlist("ospf_redistribute_bgp[]")

            ospf_process_ids = [pid for pid in ospf_process_ids if pid.strip()]
            if ospf_process_ids:
                ospf = {
                    "process_id": ospf_process_ids[0],
                    "networks": [
                        {
                            "ip": ospf_networks[i],
                            "wildcard": ospf_wildcards[i],
                            "area": ospf_areas[i],
                        }
                        for i in range(len(ospf_networks))
                    ],
                    "redistribute_connected": len(ospf_redistribute_connected) > 0,
                    "redistribute_bgp": len(ospf_redistribute_bgp) > 0,
                }

            # BGP
            bgp = None
            bgp_asn = request.form.get("bgp_asn")
            bgp_networks = request.form.getlist("bgp_network[]")
            bgp_masks = request.form.getlist("bgp_mask[]")
            bgp_neighbors = request.form.getlist("bgp_neighbor[]")
            bgp_remote_as = request.form.getlist("bgp_remote_as[]")
            bgp_address_families = request.form.getlist("bgp_address_family[]")

            # NEW: Read redistribute toggles from BGP section
            redistribute_ospf_into_bgp = "redistribute_ospf_into_bgp" in request.form
            redistribute_rip_into_bgp = "redistribute_rip_into_bgp" in request.form

            if bgp_asn and bgp_address_families:
                address_family_entries = {}

                for af, net, mask, neighbor_ip, neighbor_as in zip(
                    bgp_address_families,
                    bgp_networks,
                    bgp_masks,
                    bgp_neighbors,
                    bgp_remote_as,
                ):
                    if not (af and net and mask and neighbor_ip and neighbor_as):
                        continue

                    if af not in address_family_entries:
                        address_family_entries[af] = {
                            "type": af,
                            "networks": [],
                            "neighbors": [],
                        }

                    address_family_entries[af]["networks"].append(
                        {"ip": net, "mask": _cidr_value(mask)}
                    )
                    address_family_entries[af]["neighbors"].append(
                        {"ip": neighbor_ip, "remote_as": neighbor_as}
                    )

                bgp = {
                    "as_number": bgp_asn,
                    "address_families": list(address_family_entries.values()),
                    "redistribute_ospf": redistribute_ospf_into_bgp,
                    "redistribute_rip": redistribute_rip_into_bgp,
                }

            custom_config = "\n".join(
                line_block.strip()
                for line_block in request.form.getlist("custom_config[]")
                if line_block.strip()
            )

            # Attempt to generate YAML and push via Jenkins
            try:
                create_yaml_from_form_devices(
                    device_ids=device_ids,
                    vendor_lookup=lambda target: _get_vendor_for_device(target),
                    interfaces=interfaces,
                    subinterfaces=subinterfaces,
                    vlans=vlans,
                    rip=rip,
                    ospf=ospf,
                    bgp=bgp,
                    custom_config=custom_config,
                )
                conf_gen()
                jenkins_result = push_and_monitor_jenkins()

                if jenkins_result == "SUCCESS":
                    return render_template(
                        "configure_device.html",
                        jenkins_result="jenkins_success",
                        device_id=device_id,
                        device_ids=device_ids,
                        devices=devices,
                        message="✅ Jenkins pipeline succeeded!",
                    )
                else:
                    return render_template(
                        "configure_device.html",
                        jenkins_result="jenkins_failure",
                        device_id=device_id,
                        device_ids=device_ids,
                        devices=devices,
                        submitted_form=submitted_form,
                        message=f"❌ Jenkins pipeline failed: {jenkins_result}",
                    )

            except Exception as pipeline_error:
                print("🔥 Pipeline error:", pipeline_error)
                return render_template(
                    "configure_device.html",
                    jenkins_result="jenkins_failure",
                    device_id=device_id,
                    device_ids=device_ids,
                    devices=devices,
                    submitted_form=submitted_form,
                    message=str(pipeline_error),
                )

        return render_template(
            "configure_device.html", jenkins_result=None, devices=devices, submitted_form={}, device_ids=[]
        )

    except Exception as e:
        print(f"Error in /configure-device: {e}")
        return render_template(
            "configure_device.html",
            jenkins_result="jenkins_failure",
            device_id="unknown",
            device_ids=[],
            message=str(e),
            devices=[],
            submitted_form={},
        )


@app.route("/push-config", methods=["POST"])
@login_required
@operator_required
def push_config():
    data = request.get_json()
    device_ids = data.get("device_ids") or data.get("device_id")
    if isinstance(device_ids, str):
        device_ids = [device_ids]
    device_ids = [device_id for device_id in (device_ids or []) if device_id]
    if not device_ids:
        return jsonify({"status": "error", "message": "No devices selected."})

    results = []
    for device_id in device_ids:
        push_status = push_configuration(device_id)
        results.append((device_id, push_status))

    failures = [
        f"{device_id}: {status}"
        for device_id, status in results
        if "successfully" not in status.lower()
    ]
    if failures:
        return jsonify({"status": "error", "message": " | ".join(failures)})

    pushed = ", ".join(device_id for device_id, _ in results)
    return jsonify({"status": "success", "message": f"Configuration pushed successfully to {pushed}."})


@app.route("/upload-config", methods=["POST"])
@login_required
@operator_required
def upload_config():
    """
    Handle uploaded configuration file and push to device using Netmiko
    """
    try:
        # Get form data
        device_ids = [
            device_id.strip()
            for device_id in request.form.getlist("device_id")
            if device_id.strip()
        ]
        config_file = request.files.get("config_file")

        if not device_ids:
            return jsonify({"status": "error", "message": "Select at least one device."})

        missing_vendor_devices = [
            device_id for device_id in device_ids if not _get_vendor_for_device(device_id)
        ]
        if missing_vendor_devices:
            return jsonify({"status": "error", "message": f"No vendor set for {', '.join(missing_vendor_devices)}. Please update Hosts Inventory."})

        if not config_file:
            return jsonify({"status": "error", "message": "No config file provided"})

        # Read file content
        config_content = config_file.read().decode("utf-8")

        if not config_content.strip():
            return jsonify({"status": "error", "message": "Config file is empty"})

        results = []
        for device_id in device_ids:
            device_vendor = _get_vendor_for_device(device_id)
            success, message = push_uploaded_config(device_id, device_vendor, config_content)
            results.append((device_id, success, message))

        failures = [
            f"{device_id}: {message}"
            for device_id, success, message in results
            if not success
        ]
        if failures:
            return jsonify({"status": "error", "message": " | ".join(failures)})

        pushed = ", ".join(device_id for device_id, _, _ in results)
        return jsonify({"status": "success", "message": f"Configuration pushed successfully to {pushed}."})

    except Exception as e:
        print(f"Error in /upload-config: {e}")
        return jsonify({"status": "error", "message": f"Error processing upload: {str(e)}"})


@app.route("/rollback", methods=["POST"])
@login_required
@operator_required
def rollback_device():
    """
    Rollback a device to its golden configuration
    """
    try:
        data = request.get_json()
        device_id = data.get("device_id")

        if not device_id:
            return jsonify({"status": "error", "message": "Device ID is required"})

        device_vendor = _get_vendor_for_device(device_id)
        if not device_vendor:
            return jsonify({"status": "error", "message": f"No vendor set for {device_id}. Please update the host in Hosts Inventory."})

        # Perform rollback
        success, message = rollback_to_golden_config(device_id, device_vendor)

        if success:
            return jsonify({"status": "success", "message": message})
        else:
            return jsonify({"status": "error", "message": message})

    except Exception as e:
        print(f"Error in /rollback: {e}")
        return jsonify({"status": "error", "message": f"Rollback error: {str(e)}"})


@app.route("/tools", methods=["GET", "POST"])
@login_required
def tools():
    devices = hosts_reader.get_devices()
    return render_template("tools.html", devices=devices)


@app.route("/telemetry")
@login_required
def telemetry_dashboard():
    hosts = _read_hosts_inventory()
    return render_template("telemetry.html", hosts=hosts)


@app.route("/api/telemetry/devices")
@login_required
def api_telemetry_devices():
    return jsonify({"devices": _read_hosts_inventory()})


@app.route("/api/telemetry/poll", methods=["POST"])
@login_required
def api_telemetry_poll():
    data = request.get_json() or {}
    hostname = (data.get("device") or "").strip()
    groups = data.get("groups") or ["l2", "l3", "device"]
    if not hostname:
        return jsonify({"success": False, "error": "Device is required."}), 400
    if not isinstance(groups, list):
        groups = ["l2", "l3", "device"]
    return jsonify(_run_device_telemetry(hostname, groups))


@app.route("/api/telemetry/custom", methods=["POST"])
@login_required
def api_telemetry_custom():
    data = request.get_json() or {}
    hostname = (data.get("device") or "").strip()
    group_id = (data.get("group_id") or "custom").strip()[:40]
    commands = data.get("commands") or []
    if not hostname:
        return jsonify({"success": False, "error": "Device is required."}), 400
    if not isinstance(commands, list):
        return jsonify({"success": False, "error": "Commands must be a list."}), 400
    return jsonify(_run_custom_telemetry(hostname, commands, group_id))


@app.route("/api/telemetry/poll-all", methods=["POST"])
@login_required
def api_telemetry_poll_all():
    data = request.get_json() or {}
    groups = data.get("groups") or ["l2", "l3", "device"]
    if not isinstance(groups, list):
        groups = ["l2", "l3", "device"]

    hosts = _read_hosts_inventory()
    results = []
    with ThreadPoolExecutor(max_workers=min(4, max(len(hosts), 1))) as executor:
        future_map = {
            executor.submit(_run_device_telemetry, host["hostname"], groups): host
            for host in hosts
        }
        for future in as_completed(future_map):
            results.append(future.result())

    order = {host["hostname"]: idx for idx, host in enumerate(hosts)}
    results.sort(key=lambda item: order.get(item.get("device"), 9999))
    return jsonify({
        "success": True,
        "results": results,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })


@app.route("/api/ping", methods=["POST"])
@login_required
def api_ping():
    data = request.get_json()
    source = data.get("source", "").strip()
    destination = data.get("destination", "").strip()
    username = data.get("username", "root")
    password = data.get("password", "password")

    if source == "localhost":
        success, output = ping_local(destination)
    else:
        success, output = ping_remote(source, destination, username, password)

    return jsonify({"success": success, "output": output})


@app.route("/api/show-command", methods=["POST"])
@login_required
def api_show_command():
    data = request.get_json()
    hostname = data.get("device", "")
    command = data.get("command", "")

    if not hostname or not command:
        return jsonify({"success": False, "output": "Device and command are required."})

    success, result = execute_show_command(hostname, command)
    return jsonify({"success": success, "output": result})


@app.route("/api/golden-config", methods=["POST"])
@login_required
@operator_required
def api_golden_config():
    data = request.get_json()
    select_all = data.get("select_all", False)
    hostname = data.get("device", "")

    if select_all:
        filenames = generate_configs(select_all=True)
    elif hostname:
        filenames = generate_configs(select_all=False, hostname=hostname)
    else:
        return jsonify({"success": False, "files": [], "message": "No device specified."})

    return jsonify({"success": True, "files": filenames or []})


@app.route("/ipam")
@login_required
def ipam():
    """Route to display IPAM table."""
    return render_template("ipam.html", ipam_data=ipam_reader.ipam_data, poll_interval=ipam_reader.update_interval)


@app.route("/ipam-data")
@login_required
def ipam_data_json():
    """JSON endpoint returning current IPAM data, last update time, and poll interval."""
    data = ipam_reader.ipam_data
    last_updated = data[0].get("Timestamp", None) if data else None
    return jsonify({
        "data": data,
        "last_updated": last_updated,
        "interval": ipam_reader.update_interval,
    })


@app.route("/ipam-sync-ips", methods=["POST"])
@login_required
def ipam_sync_ips():
    """Restart IPAM polling without rewriting hosts.csv management IPs."""
    try:
        subprocess.run(["sudo", "systemctl", "restart", "ipam.service"], check=True)
        return jsonify({"ok": True, "message": "IPAM poller restarted."})
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500


@app.route("/about")
@login_required
def about():
    return render_template("about.html")


@app.route("/contact")
@login_required
def contact():
    return render_template("contact.html")


GRAFANA_BASE_URL = os.environ.get("GRAFANA_BASE_URL", "http://localhost:3000")
# Use env var if explicitly set; otherwise use the known UID created by pilot.py
GRAFANA_DASHBOARD_URL = os.environ.get(
    "GRAFANA_DASHBOARD_URL",
    f"{GRAFANA_BASE_URL}/d/nautobuff-telemetry",
)
INFLUXDB_URL = os.environ.get("INFLUXDB_URL", "http://localhost:8086")


@app.context_processor
def inject_external_urls():
    """Make monitoring URLs available in every template (used by base.html sidebar)."""
    return {
        "grafana_base_url": GRAFANA_BASE_URL,
        "grafana_dashboard_url": GRAFANA_DASHBOARD_URL,
        "influxdb_url": INFLUXDB_URL,
    }


@app.route("/dashboard")
@login_required
def dashboard():
    return render_template(
        "dashboard.html",
        grafana_base_url=GRAFANA_BASE_URL,
        grafana_dashboard_url=GRAFANA_DASHBOARD_URL,
        influxdb_url=INFLUXDB_URL,
    )


def run_deployment_and_relay_config(
    deploy_command,
    relay_toggle,
    connected_device,
    connected_interface,
    connected_ip,
    helper_ip,
    mac_address,
    dhcp_server,
    new_subnet,
    range_lower,
    range_upper,
    default_gateway,
    ip_address,
):
    # Run deployment synchronously in the thread
    deploy_result = subprocess.run(deploy_command, shell=True)

    # Only proceed with DHCP configuration if the deployment was successful
    if deploy_result.returncode == 0 and relay_toggle:
        configure_dhcp_relay(
            connected_device, connected_interface, connected_ip, helper_ip
        )
        configure_dhcp_server(
            mac_address,
            dhcp_server,
            new_subnet,
            range_lower,
            range_upper,
            default_gateway,
            ip_address,
        )


def _is_port_open(port, host="127.0.0.1"):
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def _wait_for_port(port, host="127.0.0.1", timeout=12):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _is_port_open(port, host):
            return True
        time.sleep(0.4)
    return False


@app.route("/topology")
@login_required
def topology():
    if not _is_topology_running():
        _stop_containerlab_graph()
        return render_template("topology.html", graph_url=None, is_topology_running=False)

    # Always kill any stale containerlab graph process so the topology view
    # reflects the current topo.yml after any redeploy.
    _stop_containerlab_graph()
    try:
        subprocess.Popen(
            ["sudo", "containerlab", "graph", "-t", topo_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(f"[INFO] Started containerlab graph for: {topo_path}")
        _wait_for_port(50080)
    except Exception as e:
        print(f"[ERROR] Failed to start containerlab graph: {e}")

    try:
        gws = netifaces.gateways()
        default_iface = gws["default"][netifaces.AF_INET][1]
        iface_addrs = netifaces.ifaddresses(default_iface)
        interface_ip = iface_addrs[netifaces.AF_INET][0]["addr"]
    except Exception as e:
        print(f"[ERROR] Could not determine interface IP: {e}")
        interface_ip = "127.0.0.1"

    graph_url = f"http://{interface_ip}:50080"
    return render_template("topology.html", graph_url=graph_url, is_topology_running=True)


@app.route("/hosts-data")
@login_required
def hosts_data():
    """Return full host inventory from hosts.csv as JSON for the UI."""
    hosts = []
    hosts_csv_path = os.path.join(IPAM_DIR, "hosts.csv")
    try:
        with open(hosts_csv_path, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                mgmt_ip = (row.get("management_ip") or "").strip()
                subnet = (row.get("subnet_cidr") or "24").strip().lstrip("/")
                vendor = (row.get("vendor") or "arista").strip().lower()
                hosts.append({
                    "hostname": row.get("hostname", ""),
                    "management_ip": f"{mgmt_ip}/{subnet}" if mgmt_ip else "",
                    "username": row.get("username", ""),
                    "password": row.get("password", ""),
                    "vendor": vendor,
                })
    except Exception as e:
        print(f"[⚠] Could not read hosts.csv: {e}")
    return jsonify(hosts)


@app.route("/oob-network-info")
@login_required
def oob_network_info():
    """Read netcfg.yaml and return OOB network details for the UI hint."""
    netcfg_path = os.path.join(PILOT_DIR, "netcfg.yaml")
    try:
        with open(netcfg_path) as f:
            cfg = yaml.safe_load(f)
        vlans = cfg.get("network", {}).get("vlans", {})
        # Find the first VLAN interface that has addresses configured
        for iface, details in vlans.items():
            addresses = details.get("addresses", [])
            if addresses:
                host_cidr = addresses[0]
                import ipaddress
                net = ipaddress.ip_interface(host_cidr).network
                return jsonify({
                    "interface": iface,
                    "host_ip": host_cidr.split("/")[0],
                    "subnet": str(net),
                })
    except Exception as e:
        print(f"[⚠] Could not read netcfg.yaml: {e}")
    return jsonify({"interface": None, "host_ip": None, "subnet": None})


_MANAGED_SERVICES = [
    {"unit": "ipam.service",                  "label": "IPAM (SNMP Polling)"},
    {"unit": "gnmic_nautobuff.service",         "label": "gNMI Telemetry"},
    {"unit": "device_health_check.timer",      "label": "Device Health Check"},
    {"unit": "password_update.service",        "label": "Password Rotation"},
    {"unit": "ngrok.service",                  "label": "Ngrok Tunnel"},
    {"unit": "jenkins",                        "label": "Jenkins CI/CD"},
    {"unit": "influxdb",                       "label": "InfluxDB"},
    {"unit": "grafana-server",                 "label": "Grafana"},
    {"unit": "docker",                         "label": "Docker"},
]

# Include Ollama in managed services only if it is installed on this host
if shutil.which("ollama"):
    _MANAGED_SERVICES.append({"unit": "ollama", "label": "AI Chatbot (Ollama)"})

# MCP services appear in the health panel whenever both server files are present
# Note: project_root resolves to NAutoBuff/NSOT (two levels up from flask_app)
_MCP_QUERY_FILE  = os.path.join(project_root, "mcp", "dut_query.py")
_MCP_CONFIG_FILE = os.path.join(project_root, "mcp", "dut_config.py")
if os.path.exists(_MCP_QUERY_FILE) and os.path.exists(_MCP_CONFIG_FILE):
    _MANAGED_SERVICES += [
        {"unit": "nautobuff_mcp_query.service",  "label": "MCP Query Server (port 8001)"},
        {"unit": "nautobuff_mcp_config.service", "label": "MCP Config Server (port 8002)"},
    ]
_ALLOWED_UNITS = {s["unit"] for s in _MANAGED_SERVICES}


@app.route("/api/services")
@login_required
def get_service_status():
    results = []
    for svc in _MANAGED_SERVICES:
        try:
            r = subprocess.run(
                ["systemctl", "is-active", svc["unit"]],
                capture_output=True, text=True, timeout=5,
            )
            status = r.stdout.strip()
        except Exception:
            status = "unknown"
        results.append({"unit": svc["unit"], "label": svc["label"], "status": status})
    return jsonify(results)


@app.route("/api/services/control", methods=["POST"])
@login_required
def control_service():
    if not current_user.is_operator():
        return jsonify({"error": "Insufficient permissions"}), 403
    data = request.get_json(silent=True) or {}
    unit = data.get("unit", "")
    action = data.get("action", "")
    if unit not in _ALLOWED_UNITS or action not in ("start", "stop", "restart"):
        return jsonify({"error": "Invalid unit or action"}), 400
    try:
        r = subprocess.run(
            ["sudo", "systemctl", action, unit],
            capture_output=True, text=True, timeout=20,
        )
        if r.returncode != 0:
            return jsonify({"error": (r.stderr or r.stdout).strip() or f"exit {r.returncode}"}), 500
        return jsonify({"ok": True})
    except subprocess.TimeoutExpired:
        return jsonify({"error": "systemctl timed out"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/services/nuke", methods=["POST"])
@login_required
def nuke_services():
    if not current_user.is_operator():
        return jsonify({"error": "Insufficient permissions"}), 403
    errors = []
    for svc in _MANAGED_SERVICES:
        unit = svc["unit"]
        for action in ("stop", "disable"):
            subprocess.run(["sudo", "systemctl", action, unit],
                           capture_output=True, timeout=20)
        r = subprocess.run(
            ["sudo", "find", "/etc/systemd/system", "-name", unit, "-delete"],
            capture_output=True, timeout=10,
        )
        if r.returncode != 0:
            errors.append(unit)
    subprocess.run(["sudo", "systemctl", "daemon-reload"], capture_output=True, timeout=15)
    if errors:
        return jsonify({"error": f"Could not remove: {', '.join(errors)}"}), 500
    return jsonify({"ok": True})


@app.route("/api/jenkins-status")
@login_required
def jenkins_status():
    """Return the current ngrok public URL and derived Jenkins/webhook URLs."""
    from git_jenkins import find_ngrok_log_file, get_latest_ngrok_url
    log_path = find_ngrok_log_file()
    ngrok_url = get_latest_ngrok_url(log_path) if log_path else None
    job = os.environ.get("JENKINS_JOB_NAME", "NAutoBuff")
    return jsonify({
        "ngrok_url": ngrok_url,
        "jenkins_url": f"{ngrok_url}/job/{job}" if ngrok_url else None,
        "webhook_url": f"{ngrok_url}/github-webhook/" if ngrok_url else None,
    })


@app.route("/clab-health")
@login_required
def clab_health():
    try:
        client = docker.from_env()
        containers = client.containers.list(filters={"label": "containerlab"})
        if containers:
            return jsonify({"status": "up", "message": "Containerlab is up"})
        else:
            return jsonify({"status": "down", "message": "Containerlab is down"})
    except Exception as e:
        return jsonify({"status": "down", "message": f"Error: {str(e)}"})


# ── SSH Console — Socket.IO handlers ─────────────────────────────────────────

def _read_ssh_output(channel, sid):
    """Background thread: forward SSH channel output to the browser."""
    try:
        while True:
            if channel.closed:
                break
            if channel.recv_ready():
                data = channel.recv(4096).decode("utf-8", errors="replace")
                socketio.emit("ssh_output", {"data": data}, to=sid)
            else:
                time.sleep(0.02)
    except Exception:
        pass
    finally:
        socketio.emit("ssh_closed", {}, to=sid)
        _ssh_sessions.pop(sid, None)


@socketio.on("start_ssh")
def handle_start_ssh(data):
    sid = request.sid
    device_id = (data.get("device") or "").strip()
    if not device_id:
        emit("ssh_error", {"message": "No device specified."})
        return

    # Look up credentials
    hosts_csv = os.path.join(IPAM_DIR, "hosts.csv")
    management_ip = username = password = None
    try:
        with open(hosts_csv, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                if row["hostname"].strip() == device_id:
                    management_ip = row["management_ip"].strip()
                    username = row["username"].strip()
                    password = row["password"].strip()
                    break
    except Exception as e:
        emit("ssh_error", {"message": f"Could not read hosts.csv: {e}"})
        return

    if not management_ip:
        emit("ssh_error", {"message": f"Device '{device_id}' not found in inventory."})
        return

    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(
            hostname=management_ip,
            username=username,
            password=password,
            allow_agent=False,
            look_for_keys=False,
            timeout=10,
        )
        cols = int(data.get("cols", 220))
        rows = int(data.get("rows", 50))
        channel = ssh.invoke_shell(term="xterm-256color", width=cols, height=rows)
        channel.setblocking(False)
        _ssh_sessions[sid] = {"ssh": ssh, "channel": channel}
        t = Thread(target=_read_ssh_output, args=(channel, sid), daemon=True)
        t.start()
        emit("ssh_ready", {"device": device_id})
    except Exception as e:
        emit("ssh_error", {"message": str(e)})


@socketio.on("ssh_input")
def handle_ssh_input(data):
    sid = request.sid
    session = _ssh_sessions.get(sid)
    if session:
        try:
            session["channel"].send(data.get("data", ""))
        except Exception:
            pass


@socketio.on("ssh_resize")
def handle_ssh_resize(data):
    sid = request.sid
    session = _ssh_sessions.get(sid)
    if session:
        try:
            session["channel"].resize_pty(
                width=int(data.get("cols", 220)),
                height=int(data.get("rows", 50)),
            )
        except Exception:
            pass


@socketio.on("disconnect")
def handle_sio_disconnect():
    sid = request.sid
    session = _ssh_sessions.pop(sid, None)
    if session:
        try:
            session["channel"].close()
            session["ssh"].close()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    thread = Thread(target=ipam_reader.read_ipam_file, daemon=True)
    thread.start()
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"

    socketio.run(app, host="0.0.0.0", port=5555, debug=debug, allow_unsafe_werkzeug=True)

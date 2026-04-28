import os
from jinja2 import Environment, FileSystemLoader

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "golden_configs"))
TEMPLATE_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "templates"))


def _normalize_mgmt_ip(mgmt_ip, default_prefix="24"):
    mgmt_ip = (mgmt_ip or "").strip()
    if not mgmt_ip:
        return ""
    if "/" not in mgmt_ip:
        return f"{mgmt_ip}/{default_prefix}"

    ip, prefix = mgmt_ip.split("/", 1)
    ip = ip.strip()
    prefix = prefix.strip().lstrip("/")
    return f"{ip}/{prefix or default_prefix}"


def generate_day0_config(device_name, mgmt_ip, username, password):
    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR))
    template = env.get_template("day0_config.j2")
    rendered = template.render(
        device_name=device_name,
        mgmt_ip=_normalize_mgmt_ip(mgmt_ip),
        username=username,
        password=password,
    )

    os.makedirs(CONFIG_DIR, exist_ok=True)
    config_path = os.path.join(CONFIG_DIR, f"goldenconfigs_{device_name}.cfg")
    with open(config_path, "w") as f:
        f.write(rendered)
    return config_path


def generate_mgmt_day0_config(interface_count):
    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR))
    template = env.get_template("mgmt_day0_config.j2")
    rendered = template.render(total_interfaces=interface_count)

    os.makedirs(CONFIG_DIR, exist_ok=True)
    config_path = os.path.join(CONFIG_DIR, "goldenconfigs_mgmt.cfg")
    with open(config_path, "w") as f:
        f.write(rendered)
    return config_path

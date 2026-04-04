import os
import yaml
from collections import defaultdict
from day0_config import generate_day0_config, generate_mgmt_day0_config

# Final path to save the topology YAML
TOPO_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "pilot-config", "topo.yml")
)


# Custom YAML Dumper to prevent aliasing and ensure compact inline style for 2-item endpoint lists
class NoQuotesDumper(yaml.SafeDumper):
    def ignore_aliases(self, data):
        return True


def represent_inline_endpoints(dumper, data):
    if (
        isinstance(data, list)
        and len(data) == 2
        and all(isinstance(i, str) for i in data)
    ):
        return yaml.SequenceNode(
            tag="tag:yaml.org,2002:seq",
            value=[
                dumper.represent_scalar("tag:yaml.org,2002:str", i, style="")
                for i in data
            ],
            flow_style=True,
        )
    return dumper.represent_list(data)


yaml.add_representer(list, represent_inline_endpoints, Dumper=NoQuotesDumper)


def build_clab_topology(topo_name, devices, links):
    nodes = {}
    yaml_links = []
    interface_counts = defaultdict(int)

    # Add mgmt node first
    nodes["mgmt"] = {
        "kind": "ceos",
        "image": "ceos:4.33.2F",
        "startup-config": "~/projects/NAutoBuff/NSOT/golden_configs/goldenconfigs_mgmt.cfg",
    }
    interface_counts["mgmt"] = 0

    # Add user devices
    for dev in devices:
        name = dev["name"]
        kind = dev["kind"].lower()
        image = dev["image"]
        exec_lines = dev.get("exec", [])
        config = dev.get("config", "").strip()
        mgmt_ip = dev.get("mgmt_ip", "").strip()
        username = dev.get("username", "").strip()
        password = dev.get("password", "").strip()

        # Generate Day 0 config if CEOS and config not already given
        if kind == "ceos" and not config and mgmt_ip:
            config = generate_day0_config(name, mgmt_ip, username, password)

        node = {"kind": kind, "image": image}

        if kind == "ceos" and config:
            node["startup-config"] = config
        elif kind == "linux" and exec_lines:
            node["exec"] = [cmd.strip() for cmd in exec_lines if cmd.strip()]

        nodes[name] = node

    # Link mgmt to CEOS devices
    for dev in devices:
        if dev["kind"].lower() == "ceos":
            dev_name = dev["name"]
            mgmt_eth = f"eth{interface_counts['mgmt'] + 1}"
            dev_eth = "eth1"
            yaml_links.append(
                {"endpoints": [f"mgmt:{mgmt_eth}", f"{dev_name}:{dev_eth}"]}
            )
            interface_counts["mgmt"] += 1
            interface_counts[dev_name] += 1

    # Add user-specified links
    for dev1, dev2 in links:
        eth1 = f"eth{interface_counts[dev1] + 1}"
        eth2 = f"eth{interface_counts[dev2] + 1}"
        yaml_links.append({"endpoints": [f"{dev1}:{eth1}", f"{dev2}:{eth2}"]})
        interface_counts[dev1] += 1
        interface_counts[dev2] += 1

    # Always link mgmt to host:eth1
    mgmt_eth = f"eth{interface_counts['mgmt'] + 1}"
    yaml_links.append({"endpoints": [f"mgmt:{mgmt_eth}", "host:eth1"]})
    interface_counts["mgmt"] += 1

    # Compose final YAML
    mgmt_config_path = generate_mgmt_day0_config(interface_counts["mgmt"])
    nodes["mgmt"]["startup-config"] = mgmt_config_path

    topo_data = {"name": topo_name, "topology": {"nodes": nodes, "links": yaml_links}}

    os.makedirs(os.path.dirname(TOPO_PATH), exist_ok=True)
    with open(TOPO_PATH, "w") as f:
        yaml.dump(topo_data, f, sort_keys=False, Dumper=NoQuotesDumper)

    print(f"[✔] YAML generated at: {TOPO_PATH}")
    return TOPO_PATH

import os
import yaml
from jinja2 import Environment, FileSystemLoader
import sys
import time


def _has_l3_config(device):
    """Return True when this device payload includes routed L3 configuration."""
    return any(
        [
            device.get("interfaces"),
            device.get("subinterfaces"),
            device.get("ospf"),
            device.get("bgp"),
            device.get("rip"),
        ]
    )


def generate_device_configs():
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    yaml_file = os.path.join(base_dir, "templates", "devices_config.yml")
    template_dir = os.path.join(base_dir, "templates")
    config_dir = os.path.join(base_dir, "configs")

    if not os.path.exists(config_dir):
        os.makedirs(config_dir)

    while not os.path.exists(yaml_file):
        print(f"No devices_config.yml found in {yaml_file}.")
        time.sleep(2)

    try:
        with open(yaml_file) as f:
            devices = yaml.safe_load(f)["devices"]
    except Exception as e:
        print(f"Error reading YAML file: {e}")
        sys.exit(1)

    env = Environment(
        loader=FileSystemLoader(template_dir),
        trim_blocks=True,
        lstrip_blocks=True,
    )

    templates = {
        "bgp": env.get_template("bgp_template.j2"),
        "ospf": env.get_template("ospf_template.j2"),
        "rip": env.get_template("rip_template.j2"),
        "dhcp": env.get_template("dhcp_template.j2"),
        "l3_routing": env.get_template("l3_routing_template.j2"),
        "interfaces": env.get_template("interfaces_template.j2"),
        "subinterfaces": env.get_template("subinterface_template.j2"),
        "vlan": env.get_template("vlan_template.j2"),
        "bgp_cisco": env.get_template("bgp_template_cisco.j2"),
        "ospf_cisco": env.get_template("ospf_template_cisco.j2"),
        "rip_cisco": env.get_template("rip_template_cisco.j2"),
        "interfaces_cisco": env.get_template("interfaces_template_cisco.j2"),
    }

    for device in devices:
        config = ""
        clear_config = device.get("clear_config", "no")
        vendor = device.get("vendor", "arista").lower()
        hostname = device.get("hostname")

        if clear_config != "yes" and _has_l3_config(device):
            config += templates["l3_routing"].render()

        # Interfaces
        if "interfaces" in device:
            if clear_config == "yes":
                for iface in device["interfaces"]:
                    config += f"no interface {iface['type']}{iface['number']}\n"
            else:
                template_key = "interfaces_cisco" if vendor == "cisco" else "interfaces"
                config += templates[template_key].render(
                    interfaces=device["interfaces"]
                )

        # Subinterfaces
        if "subinterfaces" in device:
            config += templates["subinterfaces"].render(
                subinterfaces=device["subinterfaces"]
            )

        # VLANs
        if "vlans" in device:
            if clear_config == "yes":
                for vlan in device["vlans"]:
                    config += f"no vlan {vlan['id']}\n"
            else:
                config += templates["vlan"].render(vlans=device["vlans"])

        # OSPF
        if "ospf" in device:
            ospf_data = device["ospf"]
            if clear_config == "yes":
                config += f"no router ospf {ospf_data['process_id']}\n"
            else:
                template_key = "ospf_cisco" if vendor == "cisco" else "ospf"
                config += templates[template_key].render(
                    ospf_process=ospf_data["process_id"],
                    ospf_networks=ospf_data.get("networks", []),
                    redistribute_connected=ospf_data.get(
                        "redistribute_connected", False
                    ),
                    redistribute_bgp=ospf_data.get("redistribute_bgp", False),
                )

        # BGP
        if "bgp" in device:
            bgp_data = device["bgp"]
            if clear_config == "yes":
                config += f"no router bgp {bgp_data['as_number']}\n"
            else:
                template_key = "bgp_cisco" if vendor == "cisco" else "bgp"
                config += templates[template_key].render(bgp=bgp_data)

        # RIP
        if "rip" in device:
            rip_data = device["rip"]
            if clear_config == "yes":
                config += "no router rip\n"
            else:
                template_key = "rip_cisco" if vendor == "cisco" else "rip"
                config += templates[template_key].render(
                    rip_version=rip_data.get("version"),
                    rip_networks=rip_data.get("networks", []),
                    bgp_redistribute=rip_data.get("redistribute", {}).get("bgp", False),
                    bgp_as=device.get("bgp", {}).get("as_number", ""),
                    bgp_metric=rip_data.get("redistribute", {}).get("metric", 1),
                )

        # DHCP
        if "dhcp" in device:
            dhcp_data = device["dhcp"]
            if clear_config == "yes":
                config += "no service dhcp\n"
            else:
                config += templates["dhcp"].render(dhcp=dhcp_data)

        # Custom CLI
        if clear_config != "yes" and "custom_config" in device:
            custom_lines = [
                str(line).rstrip()
                for line in device.get("custom_config", [])
                if str(line).strip()
            ]
            if custom_lines:
                config += "\n".join(custom_lines) + "\n"

        # Write config to file
        filename = os.path.join(config_dir, f"{hostname}.cfg")
        with open(filename, "w") as config_file:
            config_file.write(config)

        print(f"✅ Configuration generated for {hostname} and saved to {filename}")


def conf_gen():
    generate_device_configs()

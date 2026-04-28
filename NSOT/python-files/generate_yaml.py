import os
import yaml


def clean_empty(data):
    """Recursively remove empty lists, dictionaries, and None values from the data."""
    if isinstance(data, dict):
        return {k: clean_empty(v) for k, v in data.items() if v not in [None, {}, []]}
    elif isinstance(data, list):
        return [clean_empty(v) for v in data if v not in [None, {}, []]]
    return data


def create_yaml_from_form(device_data, filename="devices_config.yml"):
    """Creates a YAML file from one device dict or a list of device dicts."""
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    yaml_file_path = os.path.join(base_dir, "templates", filename)

    devices = device_data if isinstance(device_data, list) else [device_data]
    cleaned_data = clean_empty({"devices": devices})

    with open(yaml_file_path, "w") as yaml_file:
        yaml_file.write("---\n")
        yaml.dump(
            cleaned_data,
            yaml_file,
            default_flow_style=False,
            sort_keys=False,
        )
    print(f"YAML file saved: {yaml_file_path}")


def build_device_data(
    device_id,
    device_vendor,
    interfaces,
    subinterfaces=None,
    ospf=None,
    bgp=None,
    vlans=None,
    rip=None,
    static_routes=None,
    dhcp=None,
    custom_config=None,
):
    """Builds device data structure with only non-empty fields."""
    device_data = {
        "hostname": device_id,
        "vendor": device_vendor,
        "clear_config": "no",
    }

    if interfaces:
        device_data["interfaces"] = [
            {
                "name": iface["type"] + iface["number"],
                "ip_address": iface.get("ip"),
                "subnet_mask": iface.get("mask"),
            }
            for iface in interfaces
            if iface.get("ip") and iface.get("mask")
        ]

    if subinterfaces:
        device_data["subinterfaces"] = [
            {
                "parent": sub["parent"],
                "id": sub["id"],
                "vlan": sub["vlan"],
                "ip": sub["ip"],
                "mask": sub["mask"],
            }
            for sub in subinterfaces
            if sub.get("parent")
            and sub.get("id")
            and sub.get("vlan")
            and sub.get("ip")
            and sub.get("mask")
        ]

    if vlans:
        device_data["vlans"] = [
            {"id": vlan["id"], "name": vlan["name"]}
            for vlan in vlans
            if vlan.get("id") and vlan.get("name")
        ]

    if ospf and ospf.get("process_id"):
        device_data["ospf"] = {
            "process_id": ospf["process_id"],
            "networks": [
                {
                    "ip": net["ip"],
                    "wildcard": net["wildcard"],
                    "area": net["area"],
                }
                for net in ospf["networks"]
                if net.get("ip") and net.get("wildcard") and net.get("area")
            ],
            "redistribute": {
                "connected": ospf.get("redistribute_connected", False),
                "bgp": ospf.get("redistribute_bgp", False),
            },
        }

    if bgp and bgp.get("as_number"):
        device_data["bgp"] = {
            "as_number": bgp["as_number"],
            "address_families": [
                {
                    "type": family["type"],
                    "networks": [
                        {"ip": net["ip"], "mask": net.get("mask")}
                        for net in family.get("networks", [])
                        if net.get("ip") and net.get("mask")
                    ],
                    "neighbors": [
                        {
                            "ip": neighbor["ip"],
                            "remote_as": neighbor["remote_as"],
                        }
                        for neighbor in family.get("neighbors", [])
                        if neighbor.get("ip") and neighbor.get("remote_as")
                    ],
                }
                for family in bgp.get("address_families", [])
                if family.get("type")
            ],
            "redistribute_ospf": bgp.get("redistribute_ospf", False),
            "redistribute_rip": bgp.get("redistribute_rip", False),
        }

    if rip and rip.get("version") and any(rip.get("networks")):
        device_data["rip"] = {
            "version": rip["version"],
            "networks": [net["ip"] for net in rip["networks"] if net.get("ip")],
            "redistribute": {
                "bgp": rip.get("redistribute", {}).get("bgp", False),
                "metric": rip.get("redistribute", {}).get("metric", 1),
            },
        }

    if static_routes:
        device_data["static_routes"] = [
            {
                "network": route["network"],
                "mask": route["mask"],
                "nexthop": route["nexthop"],
                "ad": route.get("ad"),
            }
            for route in static_routes
            if route.get("network") and route.get("mask") and route.get("nexthop")
        ]

    if custom_config:
        custom_lines = [
            line.strip()
            for line in custom_config.splitlines()
            if line.strip()
        ]
        if custom_lines:
            device_data["custom_config"] = custom_lines

    return device_data


def create_yaml_from_form_data(
    device_id,
    device_vendor,
    interfaces,
    subinterfaces=None,
    ospf=None,
    bgp=None,
    vlans=None,
    rip=None,
    static_routes=None,
    dhcp=None,
    custom_config=None,
):
    """Generates YAML configuration file from form data."""
    device_data = build_device_data(
        device_id,
        device_vendor,
        interfaces,
        subinterfaces,
        ospf,
        bgp,
        vlans,
        rip,
        static_routes,
        dhcp,
        custom_config,
    )
    create_yaml_from_form(device_data)


def create_yaml_from_form_devices(
    device_ids,
    vendor_lookup,
    interfaces,
    subinterfaces=None,
    ospf=None,
    bgp=None,
    vlans=None,
    rip=None,
    static_routes=None,
    dhcp=None,
    custom_config=None,
):
    """Generates one YAML file containing the same config payload for many devices."""
    devices = [
        build_device_data(
            device_id,
            vendor_lookup(device_id),
            interfaces,
            subinterfaces,
            ospf,
            bgp,
            vlans,
            rip,
            static_routes,
            dhcp,
            custom_config,
        )
        for device_id in device_ids
    ]
    create_yaml_from_form(devices)

# helper/generate_config.py

import os
from jinja2 import Environment, FileSystemLoader, TemplateNotFound

_current_dir = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.abspath(os.path.join(_current_dir, "..", "..", "templates"))
L3_TEMPLATE_FILES = {
    "interfaces_template.j2",
    "interfaces_template_cisco.j2",
    "subinterface_template.j2",
    "bgp_template.j2",
    "bgp_template_cisco.j2",
    "ospf_template.j2",
    "ospf_template_cisco.j2",
    "rip_template.j2",
    "rip_template_cisco.j2",
}


def render_device_config(device_name, template_file, params):
    try:
        env = Environment(loader=FileSystemLoader(TEMPLATE_DIR))
        template = env.get_template(template_file)
    except TemplateNotFound:
        print(
            f"❌ Template file '{template_file}' not found in '{TEMPLATE_DIR}' folder."
        )
        return None

    try:
        config_text = template.render(**params)
        if template_file in L3_TEMPLATE_FILES:
            l3_template = env.get_template("l3_routing_template.j2")
            config_text = l3_template.render() + config_text
    except Exception as e:
        print(f"❌ Failed to render template '{template_file}'. Error: {str(e)}")
        return None

    # Save generated config to a file
    output_folder = "generated_configs"
    os.makedirs(output_folder, exist_ok=True)
    filename = os.path.join(output_folder, f"{device_name}_config.txt")

    try:
        with open(filename, "w") as f:
            f.write(config_text)
        print(f"✅ Configuration saved to {filename}")
    except Exception as e:
        print(f"❌ Failed to save configuration file. Error: {str(e)}")

    return config_text

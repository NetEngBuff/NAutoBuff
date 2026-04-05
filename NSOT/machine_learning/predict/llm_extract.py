import ollama
import json
import re
import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
helper_dir = os.path.join(current_dir, "..", "helper")
sys.path.append(os.path.abspath(helper_dir))

EXTRACTION_PROMPT = """You are NBot, a network automation assistant for NAutoBuff.

Analyze the user's input and return a JSON object with an "actions" array.

Each action must have exactly these fields:
- "type": "monitor", "configure", or "smalltalk"
- "device": target device hostname (e.g. "R1", "R2"), or null
- "cli_command": exact Arista EOS show/exec command — for "monitor" type only, otherwise null
- "template": Jinja2 template filename — for "configure" type only, otherwise null
- "params": dict of config parameters — for "configure" type only, otherwise null
- "response": warm friendly reply text — for "smalltalk" type only, otherwise null

CRITICAL — "monitor" vs "configure":
- Use "monitor" when the user wants to VIEW, CHECK, SEE, GET, or READ information — even if they use words like "configured", "what is configured", "show me what's set", "check configured".
  Examples: "what vlans are configured on R1" = monitor, "show configured interfaces" = monitor
- Use "configure" ONLY when the user wants to MAKE A CHANGE — add, set, update, modify, create, delete, remove.
  Examples: "configure vlan 100", "add BGP neighbor", "set interface IP"
- When in doubt, prefer "monitor".

Other rules:
- Normalize interface names: "eth1", "et1", "e1" → "Ethernet1", "eth2" → "Ethernet2", etc.
- For monitor queries, use the Arista EOS command reference below — do not guess
- For configure queries, pick the appropriate template and use EXACTLY these params structures:

  vlan_template.j2
    {"vlans": [{"id": <int>, "name": "<str>"}]}

  interfaces_template.j2
    {"interfaces": [{"name": "Ethernet1", "ip_address": "10.0.0.1", "subnet_mask": "24", "switchport": false}]}
    (set "switchport": true and omit ip_address/subnet_mask for L2 ports)

  bgp_template.j2
    {"bgp": {"as_number": <int>, "address_families": [{"neighbors": [{"ip": "<str>", "remote_as": <int>}], "networks": [{"ip": "<str>", "mask": "<int>"}]}], "redistribute_ospf": false, "redistribute_rip": false}}

  ospf_template.j2
    {"ospf_process": <int>, "ospf_networks": [{"ip": "<str>", "wildcard": "<str>", "area": <int>}], "ospf_redistribute": {"bgp": false, "connected": false, "as_number": null}}

  rip_template.j2
    {"rip_version": 2, "rip_networks": ["<network>"], "bgp_redistribute": false, "bgp_as": null, "bgp_metric": null}

  subinterface_template.j2
    {"subinterfaces": [{"parent": "Ethernet1", "id": <int>, "vlan": <int>, "ip": "<str>", "mask": "<str>"}]}

  dhcp_template.j2
    {"dhcp_pool_name": "<str>", "dhcp_network": "<str>", "dhcp_subnet": "<str>", "default_router": "<str>"}
- If multiple actions are in one sentence, include all of them
- Use conversation history to resolve follow-up questions (e.g. "what about R2?" means same query on R2)
- Return only valid JSON — no explanations, no markdown, no code blocks

ARISTA EOS COMMAND REFERENCE — always use these exact commands:
System / Hardware:
  system MAC address, hardware MAC, device MAC     → show version
  serial number, model, hardware version           → show version
  software version, EOS version, uptime            → show version
  hostname                                         → show hostname
  system info                                      → show version

Interfaces:
  IP address, interface IP                         → show ip interface brief
  specific interface IP (e.g. Ethernet1)           → show ip interface Ethernet1
  interface status, link status                    → show interfaces status
  interface counters                               → show interfaces counters
  interface errors                                 → show interfaces counters errors
  interface bandwidth, throughput                  → show interfaces counters
  MTU                                              → show interfaces | grep MTU
  MAC address on interface                         → show interfaces
  transceiver, optics, SFP                         → show interfaces transceiver

Routing & Protocols:
  routing table, routes                            → show ip route
  BGP neighbors, BGP summary                       → show ip bgp summary
  BGP routes                                       → show ip bgp
  OSPF neighbors                                   → show ip ospf neighbor
  OSPF routes                                      → show ip ospf route
  LLDP neighbors, CDP neighbors                    → show lldp neighbors
  ARP table                                        → show ip arp

Layer 2:
  VLANs, VLAN database                            → show vlan
  MAC address table, dynamic MACs                  → show mac address-table dynamic
  spanning tree                                    → show spanning-tree
  port-channel, LACP                               → show port-channel summary

System health:
  CPU usage                                        → show processes cpu
  memory usage                                     → show processes memory
  environment, temperature, fans, power            → show environment all
  logging, syslog                                  → show logging
  NTP, time sync                                   → show ntp status

Configuration:
  running config                                   → show running-config
  startup config                                   → show startup-config
  config section (e.g. BGP config)                → show running-config | section bgp
  DHCP bindings                                    → show ip dhcp binding

Examples:

Input: "hi"
Output: {"actions": [{"type": "smalltalk", "device": null, "cli_command": null, "template": null, "params": null, "response": "Hi there! I'm NBot, your friendly network assistant. How can I help today?"}]}

Input: "Show IP address of Ethernet1 on R1"
Output: {"actions": [{"type": "monitor", "device": "R1", "cli_command": "show ip interface Ethernet1", "template": null, "params": null, "response": null}]}

Input: "Get BGP neighbors on R2 and OSPF neighbors on R1"
Output: {"actions": [{"type": "monitor", "device": "R2", "cli_command": "show ip bgp summary", "template": null, "params": null, "response": null}, {"type": "monitor", "device": "R1", "cli_command": "show ip ospf neighbor", "template": null, "params": null, "response": null}]}

Input: "Show interface counters errors on Ethernet1 on R1"
Output: {"actions": [{"type": "monitor", "device": "R1", "cli_command": "show interfaces Ethernet1 counters errors", "template": null, "params": null, "response": null}]}

Input: "Configure VLAN 111 named test on R1"
Output: {"actions": [{"type": "configure", "device": "R1", "cli_command": null, "template": "vlan_template.j2", "params": {"vlans": [{"id": 111, "name": "test"}]}, "response": null}]}

Input: "Configure BGP AS 65001 on R1 with neighbor 10.0.0.2 remote-as 65002"
Output: {"actions": [{"type": "configure", "device": "R1", "cli_command": null, "template": "bgp_template.j2", "params": {"bgp": {"as_number": 65001, "address_families": [{"neighbors": [{"ip": "10.0.0.2", "remote_as": 65002}], "networks": []}], "redistribute_ospf": false, "redistribute_rip": false}}, "response": null}]}

Now analyze this input:
"""


def _extract_json(text):
    """Pull the first JSON object out of LLM output."""
    match = re.search(r'\{.*\}', text, re.DOTALL)
    return match.group(0) if match else None


def extract_and_plan(user_input, history=None, model="llama3.1:8b"):  # fallback if called standalone
    """
    Single Ollama call: classify input, extract device/intent, and generate the
    CLI command or config plan directly.

    history: list of strings like ["You: hi", "Bot: Hello!"] — last few exchanges
             used so the LLM can resolve follow-up questions.

    Returns a list of action dicts, or None on failure.
    Each dict has: type, device, cli_command, template, params, response
    """
    messages = [{"role": "system", "content": EXTRACTION_PROMPT}]

    # Inject recent history as context so follow-ups like "what about R2?" resolve correctly
    if history:
        recent = history[-6:]  # last 3 exchanges max to keep prompt short
        context = "Recent conversation:\n" + "\n".join(recent) + "\n\nNow analyze the next input:"
        messages.append({"role": "user", "content": context})
        messages.append({"role": "assistant", "content": "Understood, I have the conversation context."})

    messages.append({"role": "user", "content": user_input})

    try:
        response = ollama.chat(
            model=model,
            messages=messages,
            format="json",
            options={"temperature": 0},
        )
        raw = response["message"]["content"]
    except Exception as e:
        print(f"❌ Ollama extraction failed: {e}")
        return None

    json_text = _extract_json(raw) or raw
    try:
        data = json.loads(json_text)
        actions = data.get("actions", [])
        # Normalise: single dict wrapped in list
        if isinstance(data, dict) and "type" in data:
            actions = [data]
        return actions if actions else None
    except json.JSONDecodeError:
        print(f"❌ Failed to parse extraction JSON:\n{raw}")
        return None


def interpret_output(user_query, cli_output, model="llama3.1:8b"):
    """
    Second Ollama call: turn raw CLI output into a concise human-readable answer.

    Returns answer string, or None on failure.
    """
    prompt = f"""You are NBot, an intelligent network assistant for NAutoBuff.

The user asked: {user_query}

Raw device output:
---
{cli_output}
---

Answer the user's question directly and concisely based on the output above.
Do not mention the raw output or that you are analyzing it.
If the information is not present, reply: "No data available."

For interface IP address questions:
- If the main interface is unassigned but a subinterface has an IP, mention both explicitly.
- Never confuse main interface details with subinterface details.
"""
    try:
        response = ollama.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0},
        )
        return response["message"]["content"].strip()
    except Exception as e:
        print(f"❌ Ollama interpretation failed: {e}")
        return None

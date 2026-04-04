import ollama
import sys
import json
import re
import os

current_dir = os.path.dirname(os.path.abspath(__file__))
models_dir = os.path.join(current_dir, "..", "..", "machine_learning", "prompts")
helper_dir = os.path.join(current_dir, "..", "helper")
sys.path.append(os.path.abspath(helper_dir))
from ollama_utils import stop_ollama_model


def read_prompt_template():
    """Reads the static prompt template from prompts/extract_fields.txt"""
    with open(os.path.join(models_dir, "extract_fields.txt"), "r") as file:
        return file.read()


def extract_json_from_text(text):
    """Extract JSON block from LLM output even if extra text is present"""
    json_match = re.search(r"(\[.*\]|\{.*\})", text, re.DOTALL)
    if json_match:
        return json_match.group(1)
    else:
        return None


def process_cli_output(user_query, cli_output):
    """Ask Llama to intelligently extract information from CLI output based on user query."""
    prompt = f"""
You are an intelligent network assistant named NBot working for the NAutoBuff developed by Ashwin Chandrasekaran.

Given:
- A user's question about the network.
- The raw technical output from the network device.

Task:
- Analyze the device output carefully.
- Extract **only the information needed to directly answer the user's question**.
- Respond **naturally** and **briefly**, as if you already knew the answer.
- **Do not mention the CLI output** or that you are analyzing raw output.
- If the requested information is not available, simply reply: "No data available."

When answering about interface IP addresses, be very careful:

- First check the requested main interface (e.g., Ethernet1).
- If the main interface shows "unassigned", check if there are any subinterfaces (e.g., Ethernet1.100, Ethernet1.200) that are assigned an IP address.
- If a subinterface has the IP address, explicitly mention that the main interface is unassigned but the subinterface has the IP.

Example:
User asks: "What is the IP of Ethernet1 on R1?"
- If Ethernet1 is unassigned and Ethernet1.100 has IP 10.0.101.1/24, you must answer:
  ➔ "Ethernet1 is unassigned, but the subinterface Ethernet1.100 has the IP address 10.0.101.1/24."

- If Ethernet1 directly has an IP, simply state it normally.

Be accurate, cautious, and clear. Never confuse main interface details with subinterface details.

If no IP is assigned to either, respond: "No IP address is assigned to Ethernet1 or its subinterfaces."

User's Question:
---
{user_query}
---

Device Output:
---
{cli_output}
---

Provide the final answer below:
"""
    response = ollama.chat(
        model="llama3.1",
        messages=[{"role": "user", "content": prompt}],
        options={"temperature": 0},
    )

    answer = response["message"]["content"]
    stop_ollama_model("llama3.1")
    return answer


def real_llm_extract(user_input):
    """Sends user input to Llama 3.1 via Ollama and extracts intent, device, monitor, configure"""
    prompt_template = read_prompt_template()
    final_prompt = prompt_template.replace("{user_input_here}", user_input)

    response = ollama.chat(
        model="llama3.1",
        messages=[{"role": "user", "content": final_prompt}],
        options={"temperature": 0},
    )

    model_output = response["message"]["content"]
    json_text = extract_json_from_text(model_output)

    if not json_text:
        print(model_output)
        stop_ollama_model("llama3.1")
        return None

    try:
        extracted_fields = json.loads(json_text)

        if isinstance(extracted_fields, dict):
            extracted_fields = [extracted_fields]

        print("\n✅ Extracted Actions:")
        for idx, action in enumerate(extracted_fields):
            print(f"\n🔹 Action {idx+1}:")
            print(f"Intent: {action.get('intent')}")
            print(f"Device: {action.get('device')}")
            print(f"Monitor: {action.get('monitor')}")
            print(f"Configure: {action.get('configure')}")

        return extracted_fields

    except json.JSONDecodeError:
        print("\n❌ Failed to parse extracted JSON. Raw output:")
        print(json_text)
        return None

    finally:
        stop_ollama_model("llama3.1")

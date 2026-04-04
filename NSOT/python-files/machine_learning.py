import ollama
import os
import requests
import csv
from netmiko import ConnectHandler
from flask import jsonify

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
NSOT_DIR = os.path.dirname(BASE_DIR)
NAutoBuff_DIR = os.path.dirname(NSOT_DIR)
MISC_DIR = os.path.join(NSOT_DIR, "misc")
INSTRUCTIONS_FILE = os.path.join(MISC_DIR, "instructions.txt")
IPAM_DIR = os.path.join(NSOT_DIR, "IPAM")
HOSTS_CSV = os.path.join(IPAM_DIR, "hosts.csv")


# Stream version
async def ask_llama_for_command_stream(model: str, user_input: str):
    client = ollama.AsyncClient()
    with open(INSTRUCTIONS_FILE, "r") as file:
        system_content = file.read()
    messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_input},
    ]
    response = await client.chat(model=model, messages=messages, stream=True)
    async for chunk in response:
        if "message" in chunk and "content" in chunk["message"]:
            yield chunk["message"]["content"]


# Full collected version
async def ask_llama_for_command_full(model: str, user_input: str):
    content = ""
    async for chunk in ask_llama_for_command_stream(model, user_input):
        content += chunk
    return content.strip()


# Summarization stream version
async def ask_llama_to_summarize_stream(
    model: str, user_question: str, cli_output: str
):
    client = ollama.AsyncClient()
    summarization_prompt = f"""
User Question: {user_question}

Here is the raw CLI Output:
{cli_output}

Summarize in human readable clean English. Focus only on relevant details.
"""
    messages = [{"role": "user", "content": summarization_prompt}]
    response = await client.chat(model=model, messages=messages, stream=True)
    async for chunk in response:
        if "message" in chunk and "content" in chunk["message"]:
            yield chunk["message"]["content"]


# Summarization full collected version
async def ask_llama_to_summarize_full(model: str, user_question: str, cli_output: str):
    content = ""
    async for chunk in ask_llama_to_summarize_stream(model, user_question, cli_output):
        content += chunk
    return content.strip()


def send_to_backend(device: str, command: str):
    """Send device + command to /run-command backend route."""
    payload = {"device": device, "command": command}

    try:
        response = requests.post("http://localhost:5555/run-command", json=payload)

        if response.status_code == 200:
            output = response.json()["output"]
            return output
        else:
            print("\n❌ Backend error:", response.text)
            return None
    except Exception as e:
        print("\n❌ Request error:", str(e))
        return None


# Backend device connection
def lookup_device(device_name):
    with open(HOSTS_CSV, "r") as file:
        reader = csv.DictReader(file)
        for row in reader:
            if row["hostname"].lower() == device_name.lower():
                return {
                    "ip": row["management_ip"],
                    "username": row["username"],
                    "password": row["password"],
                }
    return None


def run_command_on_device(device, command):
    device_info = lookup_device(device)
    if not device_info:
        return jsonify({"error": f"Device {device} not found in hosts.csv"}), 404

    try:
        connection = ConnectHandler(
            device_type="arista_eos",
            host=device_info["ip"],
            username=device_info["username"],
            password=device_info["password"],
        )
        connection.enable()

        output = connection.send_command(command)
        connection.disconnect()

        return jsonify({"output": output})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

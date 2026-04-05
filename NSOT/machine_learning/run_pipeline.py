from predict.llm_extract import extract_and_plan, interpret_output
from helper.fetch_show import connect_and_run_command
from helper.generate_config import render_device_config


def run_pipeline(user_input):
    actions = extract_and_plan(user_input)

    if not actions:
        print("⚠️  Could not understand the request.")
        return

    for action in actions:
        action_type = action.get("type")
        device = action.get("device")

        if action_type == "smalltalk":
            print(f"\n🤖 NBot: {action.get('response')}")

        elif action_type == "monitor":
            cli_command = action.get("cli_command")
            if not cli_command or not device:
                print("⚠️  Missing command or device for monitor action.")
                continue

            print(f"\n📡 [{device}] Running: {cli_command}")
            output = connect_and_run_command(device, cli_command)

            if output:
                answer = interpret_output(user_input, output)
                print(f"\n🤖 NBot: {answer}")
            else:
                print(f"⚠️  No output from {device}. Check the device is reachable.")

        elif action_type == "configure":
            template = action.get("template")
            params = action.get("params") or {}

            if not template or not device:
                print("⚠️  Missing template or device for configure action.")
                continue

            config_text = render_device_config(device, template, params)
            if config_text:
                print(f"\n✅ Config generated for {device}:\n{config_text}")
            else:
                print(f"⚠️  Failed to generate config for {device}.")

        else:
            print(f"⚠️  Unknown action type: '{action_type}'")


if __name__ == "__main__":
    print("🤖 NBot — NAutoBuff Network Assistant")
    print("Type 'exit' to quit.\n")
    while True:
        user_query = input("You: ").strip()
        if user_query.lower() in ["exit", "quit"]:
            print("👋 Goodbye!")
            break
        if user_query:
            run_pipeline(user_query)

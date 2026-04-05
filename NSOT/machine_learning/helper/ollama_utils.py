import subprocess


def stop_ollama_model(model_name="llama3.2:3b"):
    try:
        subprocess.run(["ollama", "stop", model_name], check=True)
        print(f"[✔] Ollama model '{model_name}' stopped cleanly.")
    except subprocess.CalledProcessError as e:
        print(f"[⚠] Failed to stop Ollama: {e}")

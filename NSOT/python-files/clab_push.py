import subprocess


def deploy_topology(yaml_path):
    try:
        subprocess.check_output(
            ["containerlab", "destroy", "-t", yaml_path],
            stderr=subprocess.STDOUT, text=True
        )
        result = subprocess.check_output(
            ["containerlab", "deploy", "-t", yaml_path],
            stderr=subprocess.STDOUT, text=True
        )
        return True, f"✅ Topology deployed successfully!<br><pre>{result}</pre>"
    except subprocess.CalledProcessError as e:
        return False, f"❌ Error deploying topology:<br><pre>{e.output}</pre>"


def get_docker_images():
    try:
        output = subprocess.check_output(
            ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"], text=True
        )
        return output.strip().split("\n")
    except subprocess.CalledProcessError:
        return []

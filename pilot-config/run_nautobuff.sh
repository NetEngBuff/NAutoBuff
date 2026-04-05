#!/bin/bash
set -e

# 1. Load the environment (Reuse Python 3.12)
export PYENV_ROOT="$HOME/.pyenv"
export PATH="$PYENV_ROOT/bin:$PATH"
eval "$(pyenv init -)"
pyenv shell 3.12.8

# 2. Activate the existing VENV
if [ -d "venv" ]; then
    source venv/bin/activate
else
    echo "Error: venv not found! Run requirements.sh first."
    exit 1
fi

# 3. Fix Containerlab permissions
sudo setcap cap_net_admin,cap_net_raw,cap_sys_admin+ep $(which containerlab)

# 4. Jenkins credentials — loaded from .jenkins_creds (never committed to git)
# To update: edit pilot-config/.jenkins_creds
CREDS_FILE="$(dirname "$0")/.jenkins_creds"
if [ -f "$CREDS_FILE" ]; then
    source "$CREDS_FILE"
else
    echo "⚠️  Warning: $CREDS_FILE not found. Jenkins monitoring will not work."
    echo "   Create it with:"
    echo "     echo 'export JENKINS_USER=admin' > pilot-config/.jenkins_creds"
    echo "     echo 'export JENKINS_TOKEN=<your_token>' >> pilot-config/.jenkins_creds"
    echo "     echo 'export JENKINS_JOB_NAME=NAutoBuff' >> pilot-config/.jenkins_creds"
fi

echo "Launching NAutoBuff Flask App..."
echo "Running on http://0.0.0.0:5555"

# Direct path to your Flask app
"$(dirname "$0")/venv/bin/python" ~/projects/NAutoBuff/NSOT/GUI/flask_app/nautobuff.py

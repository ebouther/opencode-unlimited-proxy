import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

# -----------------------------------------------------------------------------
# Configuration Paths
# -----------------------------------------------------------------------------
HOME_DIR = Path.home()
OPENCODE_CONFIG_DIR = HOME_DIR / ".config" / "opencode"
OPENCODE_CONFIG_FILE = OPENCODE_CONFIG_DIR / "opencode.jsonc"

LOCAL_PROVIDER_ENTRY = {
    "options": {
        "baseURL": "http://127.0.0.1:8000/v1"
    },
    "name": "OpenCode Zen Local Proxy"
}

def log(msg: str, status: str = "INFO"):
    print(f"[{status}] {msg}")

def check_python_version():
    log("Checking Python version...")
    if sys.version_info < (3, 8):
        log("Python 3.8 or higher is required.", "ERROR")
        sys.exit(1)
    log("Python version OK.")

def install_dependencies():
    log("Installing Python dependencies (fastapi, uvicorn, pydantic)...")
    req_file = Path(__file__).parent / "requirements.txt"
    try:
        if req_file.exists():
            subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", str(req_file)])
        else:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "fastapi", "uvicorn", "pydantic"])
        log("Dependencies installed successfully.")
    except Exception as e:
        log(f"Failed to install dependencies: {e}", "ERROR")

def check_warp_cli():
    log("Checking Cloudflare WARP CLI installation...")
    warp_path = shutil.which("warp-cli")
    if not warp_path:
        candidates = [
            r"C:\Program Files\Cloudflare\Cloudflare WARP\warp-cli.exe",
            r"C:\Program Files (x86)\Cloudflare\Cloudflare WARP\warp-cli.exe",
            "/usr/bin/warp-cli",
            "/usr/local/bin/warp-cli",
            "/bin/warp-cli",
        ]
        for candidate in candidates:
            if os.path.exists(candidate):
                warp_path = candidate
                break
    if warp_path:
        log(f"Cloudflare WARP CLI found at: {warp_path}")
    else:
        log("warp-cli not found in PATH or standard installation locations. Ensure Cloudflare WARP is installed.", "WARN")

def setup_opencode_config():
    log("Configuring OpenCode config (~/.config/opencode/opencode.jsonc)...")
    OPENCODE_CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    config_data = {"$schema": "https://opencode.ai/config.json", "provider": {}}

    if OPENCODE_CONFIG_FILE.exists():
        try:
            content = OPENCODE_CONFIG_FILE.read_text(encoding="utf-8")
            import re
            stripped = re.sub(r'//.*|/\*.*?\*/', '', content, flags=re.DOTALL)
            config_data = json.loads(stripped)
        except Exception as e:
            log(f"Existing config parsing warning ({e}). Backing up existing config...", "WARN")
            backup_path = OPENCODE_CONFIG_FILE.with_suffix(".jsonc.bak")
            shutil.copy(OPENCODE_CONFIG_FILE, backup_path)
            log(f"Backup created at: {backup_path}")

    if "provider" not in config_data:
        config_data["provider"] = {}

    config_data["provider"]["opencode-zen-local"] = LOCAL_PROVIDER_ENTRY

    try:
        OPENCODE_CONFIG_FILE.write_text(json.dumps(config_data, indent=2), encoding="utf-8")
        log(f"OpenCode config updated successfully: {OPENCODE_CONFIG_FILE}")
    except Exception as e:
        log(f"Failed to update OpenCode config: {e}", "ERROR")

def main():
    print("=" * 60)
    print("      OpenCode IP Rotator & Zen Proxy Installer")
    print("=" * 60)

    check_python_version()
    install_dependencies()
    check_warp_cli()
    setup_opencode_config()

    print("=" * 60)
    print(" Setup completed successfully!")
    print(" You can now launch the service using:")
    print("   python server.py (and python rotator.py)")
    print(" Or using Docker:")
    print("   docker compose up -d --build")
    print("=" * 60)

if __name__ == "__main__":
    main()

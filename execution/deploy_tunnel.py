import os
import re
import sys
import time
import subprocess
import threading

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DASHBOARD_DIR = os.path.join(PROJECT_ROOT, "dashboard")
INDEX_PATH = os.path.join(DASHBOARD_DIR, "index.html")

def inject_url_and_deploy(public_url):
    print(f"\n[Tunnel] Injecting URL ({public_url}) into dashboard UI...")
    
    with open(INDEX_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    # Rewrite the config tag
    script_pattern = re.compile(
        r'(<script id="tunnel-config">).*?(</script>)',
        re.DOTALL
    )
    
    new_script_content = (
        f'\\1\n'
        f'    // Auto-injected by deploy_tunnel.py\n'
        f'    window.API_BASE = "{public_url}";\n'
        f'    window.STATIC_MODE = false;\n'
        f'\\2'
    )
    
    updated_content = script_pattern.sub(new_script_content, content)
    
    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        f.write(updated_content)
        
    print("[Surge] Deploying interactive dashboard to Surge...")
    subprocess.run(["npx", "-y", "surge", "dashboard", "asx-war-room-2026.surge.sh"], cwd=PROJECT_ROOT, shell=True)
    print("\n✅ Deployment successful!")
    print(f"🌍 Live Dashboard: https://asx-war-room-2026.surge.sh")
    print(f"🔒 API Tunnel link: {public_url}")
    print("Press Ctrl+C to stop the tunnel and server. Warning: When stopped, the live dashboard will not be able to trade.")


def main():
    print("="*60)
    print("ASX WAR ROOM: SECURE TUNNEL DEPLOYER")
    print("="*60)
    
    # 1. Start the API Server
    print("[API] Starting Dashboard API Server...")
    api_process = subprocess.Popen(
        [sys.executable, "execution/dashboard_api.py"],
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        shell=True if sys.platform == "win32" else False
    )
    
    # Allow API to start
    time.sleep(2)
    
    # 2. Start LocalTunnel
    print("[Tunnel] Starting LocalTunnel on port 8050...")
    lt_process = subprocess.Popen(
        ["npx", "-y", "localtunnel", "--port", "8050"],
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        shell=True
    )
    
    try:
        url_found = False
        if lt_process.stdout:
            while True:
                line = lt_process.stdout.readline()
                if not line and lt_process.poll() is not None:
                    break
                line = line.strip()
                if line:
                    if "your url is:" in line and not url_found:
                        parts = line.split("your url is: ")
                        if len(parts) > 1:
                            public_url = parts[1].strip()
                            url_found = True
                            inject_url_and_deploy(public_url)
                
    except KeyboardInterrupt:
        print("\n[Tunnel] Shutting down...")
    
    finally:
        print("Cleaning up processes...")
        api_process.terminate()
        lt_process.terminate()
        
        # Restore index.html to default state
        with open(INDEX_PATH, "r", encoding="utf-8") as f:
            content = f.read()
            
        script_pattern = re.compile(
            r'(<script id="tunnel-config">).*?(</script>)',
            re.DOTALL
        )
        default_script = (
            f'\\1\n'
            f'    // This will be replaced by deploy_tunnel.py during deploy\n'
            f'    // If empty string, it uses the host domain (i.e. localhost)\n'
            f'    window.API_BASE = ""; \n'
            f'    window.STATIC_MODE = false;\n'
            f'\\2'
        )
        updated_content = script_pattern.sub(default_script, content)
        with open(INDEX_PATH, "w", encoding="utf-8") as f:
            f.write(updated_content)

if __name__ == "__main__":
    main()

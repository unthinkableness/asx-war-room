import os
import sys
import shutil
import subprocess

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEPLOY_DIR = os.path.join(PROJECT_ROOT, "deploy")
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
DASHBOARD_DIR = os.path.join(PROJECT_ROOT, "dashboard")

# You can change this to any available surge.sh subdomain
SURGE_DOMAIN = "asx-war-room-2026.surge.sh"

def main():
    print("🚀 Preparing ASX War Room Dashboard for static deployment...")
    
    # Clean and recreate deploy directory
    if os.path.exists(DEPLOY_DIR):
        shutil.rmtree(DEPLOY_DIR)
    os.makedirs(DEPLOY_DIR)
    
    # 1. Copy dashboard HTML
    idx_src = os.path.join(DASHBOARD_DIR, "index.html")
    idx_dst = os.path.join(DEPLOY_DIR, "index.html")
    if os.path.exists(idx_src):
        shutil.copy2(idx_src, idx_dst)
        print(f"✅ Copied index.html to deploy/")
    else:
        print(f"❌ Error: index.html not found in {DASHBOARD_DIR}")
        sys.exit(1)

    # 2. Copy data directory
    deploy_data_dir = os.path.join(DEPLOY_DIR, "data")
    os.makedirs(deploy_data_dir, exist_ok=True)
    
    if os.path.exists(DATA_DIR):
        copied_files = 0
        for f in os.listdir(DATA_DIR):
            if f.endswith(".json"):
                shutil.copy2(os.path.join(DATA_DIR, f), os.path.join(deploy_data_dir, f))
                copied_files += 1
        print(f"✅ Copied {copied_files} data files to deploy/data/")
    else:
        print("⚠️ Warning: No data/ directory found to copy.")

    # 3. Create a 404 router/fallback if necessary (surge fallback)
    # By copying index.html to 200.html, surge handles client-side routing
    shutil.copy2(idx_dst, os.path.join(DEPLOY_DIR, "200.html"))

    # 4. Deploy using surge
    print(f"\n⚡ Deploying to {SURGE_DOMAIN} using Surge...")
    cmd = ["npx", "surge", DEPLOY_DIR, SURGE_DOMAIN]
    
    # Run synchronously
    try:
        # shell=True is required on Windows for npx
        subprocess.run(cmd, check=True, shell=os.name == "nt")
        print(f"\n🎉 Successfully deployed! Your live dashboard is available at:")
        print(f"👉 https://{SURGE_DOMAIN}")
    except subprocess.CalledProcessError as e:
        print(f"\n❌ Deployment failed. Make sure you are logged into Surge (run 'npx surge' manually first).")
        sys.exit(e.returncode)

if __name__ == "__main__":
    main()

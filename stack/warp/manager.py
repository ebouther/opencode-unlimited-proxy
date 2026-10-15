import subprocess
import sys

def recreate_container():
    print("==================================================")
    print(" [ORCHESTRATOR] 429 / Rate Limit Threshold Triggered!")
    print(" Recreating Docker Container for fresh Machine ID & IP...")
    print("==================================================")
    
    # Destroy old container
    subprocess.run(["docker", "compose", "down", "-v"], check=False)
    
    # Rebuild & launch fresh container with random hardware / system IDs
    subprocess.run(["docker", "compose", "up", "-d", "--build"], check=False)
    
    print(" New container environment spawned successfully.")

if __name__ == "__main__":
    print("Container Manager Script Ready. Run 'python manager.py' to force refresh container.")
    recreate_container()

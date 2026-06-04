#!/bin/bash
# setup-orcamail-routing.sh
# Adds orcamail.ai → localhost:8181 to the Cloudflare tunnel config
# Uses the same pattern as smartcontractexplainer.xyz (8080) and emojisandstickers.xyz (8189)

set -e

CONFIG="/etc/cloudflared/config.yml"
BACKUP="/etc/cloudflared/config.yml.bak-$(date +%Y%m%d-%H%M%S)"

echo "=== OrcaMail Cloudflare Tunnel Setup ==="
echo ""

# Step 1: Show current config
echo "--- Current config ($CONFIG) ---"
cat "$CONFIG"
echo ""

# Step 2: Check if orcamail.ai is already in config
if grep -q "orcamail.ai" "$CONFIG"; then
  echo "✅ orcamail.ai already exists in config. Nothing to do."
  echo "If you're still getting Error 522, check:"
  echo "  1. Is orcamail-server running?  sudo systemctl status orcamail-server"
  echo "  2. Is it listening on 8181?     ss -tlnp | grep 8181"
  echo "  3. Is Cloudflare DNS set to CNAME → 92e67ea2-8eb9-4374-ae4b-ff4d5e752632.cfargotunnel.com ?"
  exit 0
fi

# Step 3: Backup current config
echo "📦 Backing up config to $BACKUP"
sudo cp "$CONFIG" "$BACKUP"

# Step 4: Inject orcamail.ai ingress rule before the catch-all line
# The catch-all (- service: http_status:404) must always be LAST
echo "✏️  Adding orcamail.ai → localhost:8181 ingress rule..."

sudo python3 - <<'PYEOF'
import re

config_path = "/etc/cloudflared/config.yml"

with open(config_path, "r") as f:
    content = f.read()

new_rule = "  - hostname: orcamail.ai\n    service: http://localhost:8181\n"

# Insert before the catch-all line (the line with just "- service: http_status:404")
# That line has no hostname: prefix
catchall_pattern = r'(  - service: http_status:404)'
if re.search(catchall_pattern, content):
    updated = re.sub(catchall_pattern, new_rule + r'\1', content, count=1)
    with open(config_path, "w") as f:
        f.write(updated)
    print("✅ Rule inserted successfully.")
else:
    # Fallback: append to ingress section before end of file
    # Find last ingress entry and append after it
    lines = content.splitlines()
    insert_at = len(lines)
    for i, line in enumerate(lines):
        if 'ingress:' in line:
            insert_at = i + 1
    # Find last service: line and insert after it
    last_service = -1
    for i, line in enumerate(lines):
        if '    service:' in line:
            last_service = i
    if last_service >= 0:
        lines.insert(last_service + 1, new_rule.rstrip())
        with open(config_path, "w") as f:
            f.write("\n".join(lines) + "\n")
        print("✅ Rule appended after last service entry.")
    else:
        print("❌ Could not find insertion point in config. Please edit manually.")
        print("Add this to the ingress: section BEFORE the catch-all line:")
        print(new_rule)
        exit(1)
PYEOF

echo ""
echo "--- Updated config ---"
cat "$CONFIG"
echo ""

# Step 5: Validate the config
echo "🔍 Validating cloudflared config..."
cloudflared tunnel ingress validate 2>&1 || echo "⚠️  Validation command failed (may need tunnel name) — proceeding anyway"

# Step 6: Reload cloudflared
echo ""
echo "🔄 Reloading cloudflared service..."
sudo systemctl reload cloudflared && echo "✅ cloudflared reloaded (SIGHUP — zero downtime)" \
  || { echo "⚠️  reload failed, trying restart..."; sudo systemctl restart cloudflared; }

echo ""
echo "=== Done ==="
echo ""
echo "⚠️  CLOUDFLARE DNS — Manual step required if not already done:"
echo "   Go to dash.cloudflare.com → orcamail.ai → DNS"
echo "   Add a CNAME record:"
echo "     Name:    @  (or orcamail.ai)"
echo "     Target:  92e67ea2-8eb9-4374-ae4b-ff4d5e752632.cfargotunnel.com"
echo "     Proxy:   ✅ Proxied (orange cloud)"
echo ""
echo "   If orcamail.ai is on a DIFFERENT Cloudflare account than the tunnel,"
echo "   you'll need to either move it to the same account or use a named tunnel route:"
echo "     cloudflared tunnel route dns smart-contract orcamail.ai"
echo ""
echo "🔍 Verify orcamail-server is running on 8181:"
echo "   sudo systemctl status orcamail-server"
echo "   ss -tlnp | grep 8181"

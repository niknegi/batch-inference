#!/bin/bash
# One-time DigitalOcean Droplet setup for git push → auto deploy.
# Run on the Droplet as root (or with sudo):
#   curl -fsSL ... | bash
#   OR: scp deploy/setup-droplet.sh root@DROPLET_IP: && ssh root@DROPLET_IP 'bash setup-droplet.sh'
set -euo pipefail

REPO_NAME="batch-inference"
WORK_TREE="${DEPLOY_WORK_TREE:-/opt/${REPO_NAME}}"
BARE_REPO="${DEPLOY_BARE_REPO:-/opt/${REPO_NAME}.git}"
HOOK_SRC_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "==> Creating bare repo at ${BARE_REPO}"
mkdir -p "$(dirname "$BARE_REPO")"
if [ ! -d "$BARE_REPO" ]; then
  git init --bare "$BARE_REPO"
fi

echo "==> Creating work tree at ${WORK_TREE}"
mkdir -p "$WORK_TREE"

echo "==> Installing post-receive hook"
install -m 0755 "${HOOK_SRC_DIR}/post-receive" "${BARE_REPO}/hooks/post-receive"

# Allow hook to find paths even when env is minimal
sed -i "s|^WORK_TREE=.*|WORK_TREE=\"${WORK_TREE}\"|" "${BARE_REPO}/hooks/post-receive" 2>/dev/null || true

cat > "${BARE_REPO}/hooks/post-receive" <<EOF
#!/bin/bash
set -euo pipefail

WORK_TREE="${WORK_TREE}"
BARE_REPO="${BARE_REPO}"
BRANCH_MAIN="refs/heads/master"
BRANCH_ALT="refs/heads/main"

deploy_ref=""
while read -r _oldrev newrev refname; do
  if [ "\$refname" = "\$BRANCH_MAIN" ] || [ "\$refname" = "\$BRANCH_ALT" ]; then
    if [ "\$newrev" != "0000000000000000000000000000000000000000" ]; then
      deploy_ref="\$refname"
    fi
  fi
done

if [ -z "\$deploy_ref" ]; then
  echo "post-receive: no master/main update — skipping deploy"
  exit 0
fi

echo "post-receive: deploying \${deploy_ref} → \${WORK_TREE}"
mkdir -p "\$WORK_TREE"
GIT_WORK_TREE="\$WORK_TREE" GIT_DIR="\$BARE_REPO" git checkout -f "\${deploy_ref#refs/heads/}"
cd "\$WORK_TREE"

if [ ! -f .env ]; then
  echo "WARN: \${WORK_TREE}/.env missing — create it before relying on this deploy"
fi

if command -v docker >/dev/null 2>&1; then
  echo "post-receive: docker compose up --build -d"
  docker compose up --build -d
  docker compose ps
else
  echo "post-receive: docker not found"
  exit 1
fi

echo "post-receive: deploy complete"
EOF
chmod +x "${BARE_REPO}/hooks/post-receive"

echo ""
echo "Setup complete."
echo ""
echo "On this Droplet, create ${WORK_TREE}/.env with production secrets."
echo ""
echo "On your laptop, add the Droplet remote and push:"
echo "  git remote add droplet root@YOUR_DROPLET_IP:${BARE_REPO}"
echo "  git push droplet master"
echo ""
echo "Or use SSH URL form:"
echo "  git remote add droplet ssh://root@YOUR_DROPLET_IP${BARE_REPO}"
echo "  git push droplet master"

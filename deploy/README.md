# Deploy to DigitalOcean Droplet via git push (post-receive)
#
# 1. SSH to Droplet and install Docker if needed, then clone this repo once
#    (or copy deploy/) and run setup:
#
#    git clone https://github.com/niknegi/batch-inference.git /tmp/batch-setup
#    sudo bash /tmp/batch-setup/deploy/setup-droplet.sh
#
# 2. Create /opt/batch-inference/.env (production secrets). Never commit it.
#
# 3. From your laptop:
#
#    git remote add droplet root@YOUR_DROPLET_IP:/opt/batch-inference.git
#    git push droplet master
#
# Pushing master (or main) checks out into /opt/batch-inference and runs:
#    docker compose up --build -d

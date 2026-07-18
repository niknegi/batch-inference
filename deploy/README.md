# Deploy to DigitalOcean Droplet

## Preferred: GitHub Actions CD (push to master)

Configure these **GitHub Actions secrets** (never commit private keys):

| Secret | Example |
|--------|---------|
| `DROPLET_HOST` | `167.71.233.238` |
| `DROPLET_USER` | `root` |
| `DROPLET_SSH_KEY` | contents of the private key authorized on the Droplet |

Then push to `master`/`main`. The CD workflow tests, publishes to GHCR, SSHes to the Droplet, pulls the repo, sets `GIT_SHA` / `BUILD_ID` / `BUILT_AT` in `.env`, runs `docker compose up --build -d`, and checks `/health`.

## Optional: git push to Droplet (post-receive)

1. SSH to Droplet and install Docker if needed, then clone this repo once
   (or copy deploy/) and run setup:

   ```bash
   git clone https://github.com/niknegi/batch-inference.git /tmp/batch-setup
   sudo bash /tmp/batch-setup/deploy/setup-droplet.sh
   ```

2. Create `/opt/batch-inference/.env` (production secrets). Never commit it.

3. From your laptop:

   ```bash
   git remote add droplet root@167.71.233.238:/opt/batch-inference.git
   git push droplet master
   ```

Pushing master (or main) checks out into `/opt/batch-inference`, updates build
env vars, and runs `docker compose up --build -d`.

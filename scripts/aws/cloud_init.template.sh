#!/usr/bin/env bash
# EC2 first-boot bootstrap — values injected by scripts/aws/generate-cloud-init.sh
set -euxo pipefail

export DEBIAN_FRONTEND=noninteractive

# 1. Update packages and configure 2GB swap space to prevent memory exhaustion
apt-get update && apt-get upgrade -y
if [ ! -f /swapfile ]; then
  fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# 2. Install Docker Engine & Compose plugin (ARM64 native)
curl -fsSL https://get.docker.com | sh
usermod -aG docker ubuntu
systemctl enable docker
systemctl start docker

# 3. Install Cloudflare Tunnel (ARM64 deb)
curl -L --output /tmp/cloudflared.deb \
  https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64.deb
dpkg -i /tmp/cloudflared.deb
rm -f /tmp/cloudflared.deb

# 4. Configure and start tunnel before Docker build (avoids 530 while image builds)
mkdir -p /etc/cloudflared
cat << 'EOF_CREDS' > /etc/cloudflared/__TUNNEL_ID__.json
__TUNNEL_CREDENTIALS_JSON_CONTENT__
EOF_CREDS
chmod 600 /etc/cloudflared/__TUNNEL_ID__.json

cat << 'EOF_CF' > /etc/cloudflared/config.yml
tunnel: __TUNNEL_ID__
credentials-file: /etc/cloudflared/__TUNNEL_ID__.json

ingress:
  - hostname: __APP_HOST__
    service: http://localhost:8765
  - service: http_status:404
EOF_CF

cloudflared --config /etc/cloudflared/config.yml service install
systemctl daemon-reload
systemctl enable --now cloudflared

# 5. Clone repository into ubuntu user home directory
install -d -o ubuntu -g ubuntu /home/ubuntu/assure/data
if [ ! -d /home/ubuntu/assure/.git ]; then
  sudo -u ubuntu git clone --branch __GIT_BRANCH__ --depth 1 __GIT_CLONE_URL__ /home/ubuntu/assure
fi
cd /home/ubuntu/assure

if [ -f /tmp/assure.env.bootstrap ]; then
  install -o ubuntu -g ubuntu -m 600 /tmp/assure.env.bootstrap /home/ubuntu/assure/__ENV_FILE__
fi

# 6. Build and launch container (localhost only)
sudo -u ubuntu docker compose __COMPOSE_FILES__ up -d --build assure-app

# 7. Auto-heal cron: restart via compose if loopback health fails (not docker ps grep)
( crontab -l 2>/dev/null | grep -v '127.0.0.1:8765/health' | grep -v 'docker compose up -d assure-app' || true
  echo '*/5 * * * * curl -sf http://127.0.0.1:8765/health >/dev/null || (cd /home/ubuntu/assure && docker compose __COMPOSE_FILES__ rm -f -s assure-app 2>/dev/null; docker compose __COMPOSE_FILES__ up -d assure-app)'
) | crontab -

# 8. Unattended security updates with scheduled 03:00 UTC reboots
apt-get install -y unattended-upgrades
systemctl enable --now unattended-upgrades
grep -q 'Automatic-Reboot "true"' /etc/apt/apt.conf.d/50unattended-upgrades || \
  echo 'Unattended-Upgrade::Automatic-Reboot "true";' >> /etc/apt/apt.conf.d/50unattended-upgrades
grep -q 'Automatic-Reboot-Time "03:00"' /etc/apt/apt.conf.d/50unattended-upgrades || \
  echo 'Unattended-Upgrade::Automatic-Reboot-Time "03:00";' >> /etc/apt/apt.conf.d/50unattended-upgrades

echo "Assure bootstrap complete at $(date -u +"%Y-%m-%dT%H:%M:%SZ")" >> /var/log/assure-bootstrap.log

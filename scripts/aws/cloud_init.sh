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

# 4. Clone repository into ubuntu user home directory
install -d -o ubuntu -g ubuntu /home/ubuntu/assure/data
if [ ! -d /home/ubuntu/assure/.git ]; then
  sudo -u ubuntu git clone https://github.com/orhgor/assure.git /home/ubuntu/assure
fi
cd /home/ubuntu/assure

# 5. Build and launch container in background (production compose, localhost only)
sudo -u ubuntu docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build assure-app

# 6. Auto-heal cron: restart container every 5m if it ever stops
( crontab -l 2>/dev/null | grep -v 'docker compose up -d assure-app' || true
  echo '*/5 * * * * if ! docker ps | grep -q assure-app; then cd /home/ubuntu/assure && docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d assure-app; fi'
) | crontab -

# 7. Unattended security updates with scheduled 03:00 UTC reboots
apt-get install -y unattended-upgrades
systemctl enable --now unattended-upgrades
grep -q 'Automatic-Reboot "true"' /etc/apt/apt.conf.d/50unattended-upgrades || \
  echo 'Unattended-Upgrade::Automatic-Reboot "true";' >> /etc/apt/apt.conf.d/50unattended-upgrades
grep -q 'Automatic-Reboot-Time "03:00"' /etc/apt/apt.conf.d/50unattended-upgrades || \
  echo 'Unattended-Upgrade::Automatic-Reboot-Time "03:00";' >> /etc/apt/apt.conf.d/50unattended-upgrades

# 8. Configure Cloudflare Tunnel credentials and Ingress routing to port 8765
mkdir -p /etc/cloudflared
cat << 'EOF_CREDS' > /etc/cloudflared/3c71a11e-e98b-4f11-802e-8674b8bca524.json
{"AccountTag":"381b292d419f2efdc1c85a3636268e91","TunnelSecret":"6gDxW/J9KI4qW37rXBsuFkdsaYuF+1E4vDuMn17zuKQ=","TunnelID":"3c71a11e-e98b-4f11-802e-8674b8bca524","Endpoint":""}
EOF_CREDS
chmod 600 /etc/cloudflared/3c71a11e-e98b-4f11-802e-8674b8bca524.json

cat << 'EOF_CF' > /etc/cloudflared/config.yml
tunnel: 3c71a11e-e98b-4f11-802e-8674b8bca524
credentials-file: /etc/cloudflared/3c71a11e-e98b-4f11-802e-8674b8bca524.json

ingress:
  - hostname: app.getassureai.com
    service: http://localhost:8765
  - service: http_status:404
EOF_CF

# 9. Install and activate cloudflared systemd service
cloudflared --config /etc/cloudflared/config.yml service install
systemctl daemon-reload
systemctl enable --now cloudflared

echo "Assure bootstrap complete at $(date -u +"%Y-%m-%dT%H:%M:%SZ")" >> /var/log/assure-bootstrap.log

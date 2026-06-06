#!/bin/bash
set -e
sudo tee /etc/sudoers.d/urnetwork >/dev/null <<'EOF'
Defaults!/home/user/Документы/urnetwork-client/scripts/urnetwork-connect.sh env_keep += "USER_AUTH PASSWORD"
Defaults!/home/user/Документы/urnetwork-client/build/urnetwork-vpn env_keep += "USER_AUTH PASSWORD"
user ALL=(ALL) NOPASSWD: /home/user/Документы/urnetwork-client/scripts/urnetwork-connect.sh
user ALL=(ALL) NOPASSWD: /home/user/Документы/urnetwork-client/build/urnetwork-vpn
user ALL=(ALL) NOPASSWD: /usr/sbin/ip
EOF
sudo chmod 440 /etc/sudoers.d/urnetwork
sudo visudo -c -f /etc/sudoers.d/urnetwork
echo "Done! Password will not be required anymore."

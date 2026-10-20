#!/usr/bin/env bash
#
# provision.sh: prepare a fresh Ubuntu 22.04 server for a Snapchain read node.
#
# What to rent (any provider works):
#   - 4 or more CPU cores
#   - 16 GB of RAM or more
#   - 2 TB of SSD or NVMe storage (the database is 500-800 GB and grows)
#   - a public IPv4 address
#   - Ubuntu 22.04 (24.04 also works)
#
# Run it once, as root, on the new server:
#
#   curl -fsSL https://raw.githubusercontent.com/Ramitphi/phunk-client/main/scripts/provision.sh | sudo bash
#
# It is safe to run again. It will:
#   1. update the system packages
#   2. install Docker from Docker's own apt repository
#   3. create a non-root user (default "snapchain") with sudo and Docker access,
#      and copy root's SSH keys to it
#   4. turn on the ufw firewall: SSH, 3381/tcp (HTTP API), 3382/udp (gossip),
#      3383/tcp (gRPC)
#   5. put node.sh in the new user's home and print the next command
#
# Settings (environment variables):
#   NODE_USER   user to create          (default: snapchain)
#   SSH_PORT    SSH port to keep open   (default: 22)
#   NODE_SH_URL where to fetch node.sh  (default: this repo on GitHub)
#
# Note: ports that Docker publishes skip ufw. node.sh binds Grafana and statsd
# to 127.0.0.1 so only 3381-3383 face the internet.

set -euo pipefail

NODE_USER="${NODE_USER:-snapchain}"
SSH_PORT="${SSH_PORT:-22}"
NODE_SH_URL="${NODE_SH_URL:-https://raw.githubusercontent.com/Ramitphi/phunk-client/main/scripts/node.sh}"

export DEBIAN_FRONTEND=noninteractive

say()  { printf '\n==> %s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die()  { printf 'error: %s\n' "$*" >&2; exit 1; }

apt_get() {
    apt-get -y -q \
        -o Dpkg::Options::=--force-confdef \
        -o Dpkg::Options::=--force-confold \
        "$@"
}

check_system() {
    [ "$(id -u)" -eq 0 ] || die "run this as root (for example: sudo bash provision.sh)"
    [ -r /etc/os-release ] || die "cannot read /etc/os-release; this script is for Ubuntu"
    # shellcheck disable=SC1091
    . /etc/os-release
    [ "${ID:-}" = "ubuntu" ] || die "this script is for Ubuntu, found ${ID:-unknown}"
    if [ "${VERSION_ID:-}" != "22.04" ]; then
        warn "tested on Ubuntu 22.04, found ${VERSION_ID:-unknown}; carrying on"
    fi
    case "$NODE_USER" in
        ''|root|*[!a-z0-9_-]*) die "NODE_USER must be a plain lowercase name, not '$NODE_USER'" ;;
    esac
}

update_packages() {
    say "Updating packages"
    apt_get update
    apt_get upgrade
    apt_get install ca-certificates curl gnupg jq ufw cron sudo
}

install_docker() {
    if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
        say "Docker is already installed ($(docker --version))"
    else
        say "Installing Docker"
        # Steps from https://docs.docker.com/engine/install/ubuntu/
        install -m 0755 -d /etc/apt/keyrings
        curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
        chmod a+r /etc/apt/keyrings/docker.asc
        # shellcheck disable=SC1091
        codename="$(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")"
        echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $codename stable" \
            > /etc/apt/sources.list.d/docker.list
        apt_get update
        apt_get install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    fi

    if [ -d /run/systemd/system ]; then
        systemctl enable --now docker >/dev/null
        systemctl enable --now cron >/dev/null
    else
        warn "systemd is not running; start dockerd yourself"
    fi
}

create_user() {
    if id "$NODE_USER" >/dev/null 2>&1; then
        say "User $NODE_USER already exists"
    else
        say "Creating user $NODE_USER"
        adduser --disabled-password --gecos "" "$NODE_USER"
    fi
    usermod -aG sudo,docker "$NODE_USER"

    # Passwordless sudo, as on most cloud images. node.sh uses sudo to run the
    # official snapchain.sh, which wants root.
    sudoers="/etc/sudoers.d/90-$NODE_USER"
    printf '%s ALL=(ALL) NOPASSWD:ALL\n' "$NODE_USER" > "$sudoers.tmp"
    chmod 0440 "$sudoers.tmp"
    if visudo -cf "$sudoers.tmp" >/dev/null; then
        mv "$sudoers.tmp" "$sudoers"
    else
        rm -f "$sudoers.tmp"
        die "generated sudoers file did not validate"
    fi

    home="$(getent passwd "$NODE_USER" | cut -d: -f6)"
    copy_ssh_keys "$home"
}

# Give the new user the SSH keys of root and of whoever ran sudo. Only the key
# itself is copied: some images (AWS, for one) prefix root's keys with
# command="echo Please login as ubuntu", which would lock the new user out.
copy_ssh_keys() {
    local home="$1" keys="$1/.ssh/authorized_keys" sources=() src key found=0
    sources+=(/root/.ssh/authorized_keys)
    if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != root ] && [ "$SUDO_USER" != "$NODE_USER" ]; then
        sources+=("$(getent passwd "$SUDO_USER" | cut -d: -f6)/.ssh/authorized_keys")
    fi

    install -d -m 0700 -o "$NODE_USER" -g "$NODE_USER" "$home/.ssh"
    touch "$keys"
    for src in "${sources[@]}"; do
        [ -s "$src" ] || continue
        while IFS= read -r key; do
            found=1
            grep -qxF -- "$key" "$keys" || printf '%s\n' "$key" >> "$keys"
        done < <(grep -oE '(ssh-(rsa|ed25519|dss)|ecdsa-sha2-nistp[0-9]+|sk-[a-z0-9@.-]+) [A-Za-z0-9+/=]+( [^"]*)?$' "$src" || true)
    done
    chown "$NODE_USER:$NODE_USER" "$keys"
    chmod 0600 "$keys"
    [ "$found" -eq 1 ] || warn "found no SSH keys to copy; add one to $keys"
}

setup_firewall() {
    say "Setting up the firewall"
    ufw default deny incoming >/dev/null
    ufw default allow outgoing >/dev/null
    ufw allow "$SSH_PORT/tcp" comment 'SSH' >/dev/null
    ufw allow 3381/tcp comment 'Snapchain HTTP API' >/dev/null
    ufw allow 3382/udp comment 'Snapchain gossip' >/dev/null
    ufw allow 3383/tcp comment 'Snapchain gRPC API' >/dev/null
    if ufw --force enable >/dev/null 2>&1; then
        ufw status verbose
    else
        warn "could not enable ufw here (no firewall access?); rules are saved for next boot"
    fi
}

install_node_sh() {
    home="$(getent passwd "$NODE_USER" | cut -d: -f6)"
    target="$home/node.sh"
    # Use the node.sh next to this file when run from a clone. Under
    # "curl | bash" there is no file, so download it.
    src="${BASH_SOURCE[0]:-}"
    local_copy=""
    if [ -n "$src" ] && [ -f "$src" ]; then
        local_copy="$(dirname "$src")/node.sh"
    fi

    if [ -n "$local_copy" ] && [ -f "$local_copy" ]; then
        install -m 0755 -o "$NODE_USER" -g "$NODE_USER" "$local_copy" "$target"
    elif curl -fsSL "$NODE_SH_URL" -o "$target.tmp"; then
        install -m 0755 -o "$NODE_USER" -g "$NODE_USER" "$target.tmp" "$target"
        rm -f "$target.tmp"
    else
        rm -f "$target.tmp"
        warn "could not download node.sh from $NODE_SH_URL"
        return 0
    fi
    say "Installed $target"
}

print_next() {
    ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
    ip="${ip:-<server-ip>}"
    cat <<EOF

Done. Next, log in as $NODE_USER and start the node:

    ssh $NODE_USER@$ip
    ./node.sh start

(If $ip is a private address, use the server's public IP instead.)
EOF
    if [ -f /var/run/reboot-required ]; then
        echo
        echo "The package updates need a reboot. Run 'reboot' first, then log in."
    fi
}

check_system
update_packages
install_docker
create_user
setup_firewall
install_node_sh
print_next

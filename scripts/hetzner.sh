#!/usr/bin/env bash
#
# hetzner.sh: rent a server for a Snapchain read node on Hetzner Cloud, and
# prepare it. Run it on your own computer, not on a server.
#
#   ./hetzner.sh            create the server and its volume, then prepare it
#   ./hetzner.sh ip         print the server's address
#   ./hetzner.sh destroy    delete the server and its volume (and the node's data)
#
# Or straight from GitHub:
#
#   curl -fsSL https://raw.githubusercontent.com/Ramitphi/phunk-client/main/scripts/hetzner.sh | bash
#
# You need:
#   - the hcloud CLI (macOS: brew install hcloud; Linux and Windows:
#     https://github.com/hetznercloud/cli/releases)
#   - an API token: Hetzner Console -> your project -> Security -> API tokens
#     -> "Read & Write". Then: export HCLOUD_TOKEN=<token>
#     (or use a saved context: hcloud context create phunk)
#   - an SSH key in ~/.ssh (make one with: ssh-keygen -t ed25519)
#
# It is safe to run again: it reuses the server, volume and key it finds. It:
#   1. uploads your SSH public key to the project, if it isn't there yet
#   2. creates a volume for the node's data (default 2000 GB, ext4)
#   3. creates the server with the volume attached (default cpx42:
#      8 vCPU, 16 GB RAM, Ubuntu 22.04, in Nuremberg)
#   4. runs provision.sh on it (Docker, a "snapchain" user, the firewall,
#      node.sh), mounts the volume at /home/snapchain/snapchain, where the
#      node keeps its database, and reboots if the updates need it
#   5. prints the next command, which starts the node
#
# Settings (environment variables):
#   NAME           server name; the volume is NAME-data   (default: snapchain)
#   TYPE           server type                            (default: cpx42)
#   LOCATION       nbg1, hel1, fsn1, ash, hil or sin       (default: nbg1)
#   IMAGE          image                                  (default: ubuntu-22.04)
#   VOLUME_GB      volume size in GB                      (default: 2000)
#   SSH_KEY_FILE   public key to use        (default: first of ~/.ssh/id_*.pub)
#   PROVISION_URL  where to fetch provision.sh            (default: this repo)
#   YES=1          don't ask before creating or deleting anything
#
# This costs money from the moment the server exists. Hetzner bills by the
# hour, and "destroy" stops the bill. This script never prints your token.

set -euo pipefail

NAME="${NAME:-snapchain}"
TYPE="${TYPE:-cpx42}"
LOCATION="${LOCATION:-nbg1}"
IMAGE="${IMAGE:-ubuntu-22.04}"
VOLUME_GB="${VOLUME_GB:-2000}"
SSH_KEY_FILE="${SSH_KEY_FILE:-}"
PROVISION_URL="${PROVISION_URL:-https://raw.githubusercontent.com/Ramitphi/phunk-client/main/scripts/provision.sh}"
YES="${YES:-}"

NODE_USER=snapchain
SCRIPT_URL="https://raw.githubusercontent.com/Ramitphi/phunk-client/main/scripts/hetzner.sh"
# How to run this script again: the file when there is one, else via curl.
if [ -f "${BASH_SOURCE[0]:-}" ]; then
    self() { printf '%s %s' "${BASH_SOURCE[0]}" "$1"; }
else
    self() { printf 'curl -fsSL %s | bash -s %s' "$SCRIPT_URL" "$1"; }
fi
VOLUME="$NAME-data"
SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new)

say()  { printf '\n==> %s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die()  { printf 'error: %s\n' "$*" >&2; exit 1; }

# Ask on the terminal, so it works under "curl | bash" too.
confirm() {
    [ -n "$YES" ] && return 0
    { : </dev/tty; } 2>/dev/null || die "no terminal to ask on; set YES=1 to go ahead"
    printf '%s [y/N] ' "$1" >/dev/tty
    IFS= read -r reply </dev/tty || reply=""
    case "$reply" in y|Y|yes|Yes|YES) return 0 ;; *) die "stopped; nothing more was changed" ;; esac
}

check_tools() {
    command -v hcloud >/dev/null 2>&1 \
        || die "the hcloud CLI is not installed. macOS: brew install hcloud. Others: https://github.com/hetznercloud/cli/releases"
    command -v ssh >/dev/null 2>&1 || die "ssh is not installed"
    if [ -z "${HCLOUD_TOKEN:-}" ] && [ -z "$(hcloud context active 2>/dev/null || true)" ]; then
        die "no Hetzner API token. Make one (Console -> project -> Security -> API tokens, Read & Write), then: export HCLOUD_TOKEN=<token>"
    fi
    case "$VOLUME_GB" in ''|*[!0-9]*) die "VOLUME_GB must be a number of GB, not '$VOLUME_GB'" ;; esac
}

# ID of a server or volume, or nothing if it doesn't exist.
server_id() { hcloud server describe "$NAME" -o format='{{.ID}}' 2>/dev/null || true; }
volume_id() { hcloud volume describe "$VOLUME" -o format='{{.ID}}' 2>/dev/null || true; }

# Find the local public key, and the name it has (or gets) in the project.
ssh_key() {
    local f
    if [ -z "$SSH_KEY_FILE" ]; then
        for f in ~/.ssh/id_ed25519.pub ~/.ssh/id_ecdsa.pub ~/.ssh/id_rsa.pub; do
            if [ -f "$f" ]; then SSH_KEY_FILE="$f"; break; fi
        done
    fi
    [ -n "$SSH_KEY_FILE" ] && [ -f "$SSH_KEY_FILE" ] \
        || die "no SSH public key found. Make one with: ssh-keygen -t ed25519"

    # Hetzner lists keys by MD5 fingerprint.
    local fp name
    fp="$(ssh-keygen -l -E md5 -f "$SSH_KEY_FILE" | awk '{print $2}')"
    fp="${fp#MD5:}"
    [ -n "$fp" ] || die "could not read $SSH_KEY_FILE as an SSH public key"
    name="$(hcloud ssh-key list -o noheader -o columns=name,fingerprint | awk -v fp="$fp" '$2 == fp {print $1; exit}')"
    if [ -z "$name" ]; then
        name="phunk-$(printf '%s' "${fp//:/}" | cut -c1-12)"
        say "Uploading your SSH key ($SSH_KEY_FILE) as $name"
        hcloud ssh-key create --name "$name" --public-key-from-file "$SSH_KEY_FILE" >/dev/null
    fi
    KEY_NAME="$name"

    # Use the matching private key if it sits next to the public one.
    if [ -f "${SSH_KEY_FILE%.pub}" ]; then
        SSH_OPTS+=(-i "${SSH_KEY_FILE%.pub}")
    fi
}

create() {
    check_tools
    ssh_key

    local sid vid new=0
    sid="$(server_id)"
    vid="$(volume_id)"

    if [ -z "$sid" ] || [ -z "$vid" ]; then
        cat <<EOF

About to create on Hetzner Cloud (this costs money, billed by the hour):
  server  $NAME   type $TYPE, $IMAGE, in $LOCATION$([ -n "$sid" ] && echo "  (exists)")
  volume  $VOLUME   ${VOLUME_GB} GB, ext4$([ -n "$vid" ] && echo "  (exists)")
Check prices at https://www.hetzner.com/cloud before you go on.
Delete both later with: $(self destroy)
EOF
        confirm "Create them?"
    fi

    if [ -z "$vid" ]; then
        say "Creating volume $VOLUME (${VOLUME_GB} GB)"
        hcloud volume create --name "$VOLUME" --size "$VOLUME_GB" --location "$LOCATION" --format ext4 >/dev/null
        vid="$(volume_id)"
        [ -n "$vid" ] || die "the volume was not created"
    else
        say "Volume $VOLUME already exists"
    fi

    if [ -z "$sid" ]; then
        say "Creating server $NAME ($TYPE in $LOCATION)"
        hcloud server create --name "$NAME" --type "$TYPE" --image "$IMAGE" \
            --location "$LOCATION" --ssh-key "$KEY_NAME" --volume "$VOLUME" >/dev/null
        sid="$(server_id)"
        [ -n "$sid" ] || die "the server was not created"
        new=1
    else
        say "Server $NAME already exists"
        local on
        on="$(hcloud volume describe "$VOLUME" -o format='{{if .Server}}{{.Server.ID}}{{end}}')"
        if [ -z "$on" ]; then
            say "Attaching $VOLUME to $NAME"
            hcloud volume attach --server "$NAME" "$VOLUME" >/dev/null
        elif [ "$on" != "$sid" ]; then
            die "volume $VOLUME is attached to another server (id $on)"
        fi
    fi

    local ip
    ip="$(hcloud server ip "$NAME")"
    # A new server can get an address someone used before; forget its old host key.
    if [ "$new" -eq 1 ]; then ssh-keygen -R "$ip" >/dev/null 2>&1 || true; fi

    wait_for_ssh "$ip"
    say "Waiting for the server's first boot to finish"
    ssh -n "${SSH_OPTS[@]}" "root@$ip" 'cloud-init status --wait >/dev/null 2>&1 || true'

    say "Preparing the server (provision.sh; a few minutes)"
    # shellcheck disable=SC2029  # PROVISION_URL is meant to expand here
    ssh -n "${SSH_OPTS[@]}" "root@$ip" "curl -fsSL '$PROVISION_URL' | bash"

    say "Mounting the volume at /home/$NODE_USER/snapchain"
    # shellcheck disable=SC2029  # vid and NODE_USER are meant to expand here
    ssh "${SSH_OPTS[@]}" "root@$ip" "bash -s -- '$vid' '$NODE_USER'" <<'REMOTE'
set -euo pipefail
vid="$1" user="$2"
dev="/dev/disk/by-id/scsi-0HC_Volume_$vid"
dir="/home/$user/snapchain"
for _ in $(seq 60); do [ -e "$dev" ] && break; sleep 2; done
[ -e "$dev" ] || { echo "error: volume $vid is not attached ($dev missing)" >&2; exit 1; }
blkid "$dev" >/dev/null 2>&1 || mkfs.ext4 -q "$dev"
mkdir -p "$dir"
if ! mountpoint -q "$dir"; then
    if [ -n "$(ls -A "$dir")" ]; then
        echo "error: $dir already has files but no volume on it; move them away first" >&2
        exit 1
    fi
    grep -qF "$dev " /etc/fstab || echo "$dev $dir ext4 discard,nofail,defaults 0 0" >> /etc/fstab
    mount "$dir"
fi
chown "$user:$user" "$dir"
df -h "$dir" | tail -n 1
REMOTE

    if ssh -n "${SSH_OPTS[@]}" "root@$ip" 'test -f /var/run/reboot-required'; then
        say "Rebooting to finish the updates"
        ssh -n "${SSH_OPTS[@]}" "root@$ip" 'systemctl reboot' >/dev/null 2>&1 || true
        sleep 15
        wait_for_ssh "$ip"
    fi

    cat <<EOF

Done. $NAME is at $ip ($TYPE in $LOCATION), with a ${VOLUME_GB} GB volume for
the node's data. Next, start the node (command 1):

    ssh -t $NODE_USER@$ip ./node.sh start

Delete everything later with: $(self destroy)
EOF
}

wait_for_ssh() {
    local ip="$1" i
    say "Waiting for SSH on $ip"
    for i in $(seq 60); do
        if ssh -n "${SSH_OPTS[@]}" "root@$ip" true 2>/dev/null; then return 0; fi
        [ "$i" -eq 1 ] || printf '.'
        sleep 5
    done
    echo
    die "could not log in to root@$ip over SSH after 5 minutes"
}

show_ip() {
    check_tools
    [ -n "$(server_id)" ] || die "no server named $NAME"
    hcloud server ip "$NAME"
}

destroy() {
    check_tools
    local sid vid
    sid="$(server_id)"
    vid="$(volume_id)"
    [ -n "$sid" ] || [ -n "$vid" ] || { echo "Nothing to delete: no server $NAME, no volume $VOLUME."; return 0; }
    echo "This deletes, for good:"
    [ -z "$sid" ] || echo "  server $NAME ($(hcloud server ip "$NAME" 2>/dev/null || echo '?'))"
    [ -z "$vid" ] || echo "  volume $VOLUME, with the node's whole database"
    confirm "Delete them?"
    if [ -n "$sid" ]; then say "Deleting server $NAME"; hcloud server delete "$NAME" >/dev/null; fi
    if [ -n "$vid" ]; then say "Deleting volume $VOLUME"; hcloud volume delete "$VOLUME" >/dev/null; fi
    echo "Deleted. Your SSH key stays in the project; remove it in the console if you like."
}

case "${1:-create}" in
    create)  create ;;
    ip)      show_ip ;;
    destroy) destroy ;;
    -h|--help|help) sed -n '2,/^$/s/^# \{0,1\}//p' "${BASH_SOURCE[0]:-$0}" 2>/dev/null || true ;;
    *) die "unknown command '$1' (create, ip, destroy)" ;;
esac

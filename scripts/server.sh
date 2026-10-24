#!/usr/bin/env bash
#
# server.sh: get a server for a Snapchain read node, and prepare it. Run it
# on your own computer, not on a server.
#
#   ./server.sh hetzner          rent one on Hetzner Cloud     (hcloud CLI)
#   ./server.sh digitalocean     rent one on DigitalOcean      (doctl CLI)
#   ./server.sh aws              rent one on AWS EC2           (aws CLI)
#   ./server.sh root@203.0.113.9 use a server you already have (any provider)
#
#   ./server.sh <provider> ip       print the server's address
#   ./server.sh <provider> destroy  delete the server and its disk (and the node's data)
#
# Or straight from GitHub:
#
#   curl -fsSL https://raw.githubusercontent.com/Ramitphi/phunk-client/main/scripts/server.sh | bash -s -- hetzner
#
# What it makes: Ubuntu 22.04, 16 GB of RAM, at least 4 cores, and a 2 TB disk
# for the node's data. Then it logs in and runs provision.sh (Docker, a
# "snapchain" user with your SSH key, the firewall, node.sh), puts the data
# disk at /home/snapchain/snapchain, reboots if the updates need it, and
# prints the next command. It asks before it spends anything, and it is safe
# to run again: it reuses the server and disk it finds.
#
# You need an SSH key (make one with: ssh-keygen -t ed25519), plus:
#   hetzner       hcloud CLI; export HCLOUD_TOKEN=<token> (Console -> project
#                 -> Security -> API tokens, Read & Write)
#   digitalocean  doctl CLI; export DIGITALOCEAN_ACCESS_TOKEN=<token> (or
#                 doctl auth init)
#   aws           aws CLI v2, logged in (aws configure, or AWS_PROFILE); uses
#                 the default VPC of AWS_REGION (default: your configured one)
#   user@host     a fresh Ubuntu 22.04 server you can SSH into as root, or as
#                 a user with sudo. Set DATA_DEVICE=/dev/... to put the node's
#                 data on a second disk; it is formatted only if it is blank.
#
# Settings (environment variables):
#   NAME          server name; the disk is NAME-data       (default: snapchain)
#   TYPE          server type     (hetzner cpx42, digitalocean s-8vcpu-16gb,
#                                  aws m6i.xlarge)
#   REGION        location        (hetzner nbg1, digitalocean nyc3,
#                                  aws: your configured region)
#   DISK_GB       data disk size in GB                     (default: 2000)
#   SSH_KEY_FILE  public key to use      (default: first of ~/.ssh/id_*.pub)
#   PROVISION_URL where to fetch provision.sh              (default: this repo)
#   YES=1         don't ask before creating or deleting anything
#
# A rented server costs money from the moment it exists; "destroy" stops the
# bill. This script never prints your tokens.

set -euo pipefail

WHERE="${1:-}"
CMD="${2:-create}"
NAME="${NAME:-snapchain}"
DISK_GB="${DISK_GB:-${VOLUME_GB:-2000}}"
SSH_KEY_FILE="${SSH_KEY_FILE:-}"
PROVISION_URL="${PROVISION_URL:-https://raw.githubusercontent.com/Ramitphi/phunk-client/main/scripts/provision.sh}"
YES="${YES:-}"
TYPE="${TYPE:-}"
REGION="${REGION:-${LOCATION:-}}"

NODE_USER=snapchain
DISK="$NAME-data"
SCRIPT_URL="https://raw.githubusercontent.com/Ramitphi/phunk-client/main/scripts/server.sh"
SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new)

# Set by each provider's create: where to log in, and the data disk, if any.
IP="" SSH_USER=root DATA_DEV="" NEW=0 DESCRIBE=""

say()  { printf '\n==> %s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die()  { printf 'error: %s\n' "$*" >&2; exit 1; }

# How to run this script again: the file when there is one, else via curl.
self() {
    if [ -f "${BASH_SOURCE[0]:-}" ]; then
        printf '%s %s %s' "${BASH_SOURCE[0]}" "$WHERE" "$1"
    else
        printf 'curl -fsSL %s | bash -s -- %s %s' "$SCRIPT_URL" "$WHERE" "$1"
    fi
}

# Ask on the terminal, so it works under "curl | bash" too.
confirm() {
    [ -n "$YES" ] && return 0
    { : </dev/tty; } 2>/dev/null || die "no terminal to ask on; set YES=1 to go ahead"
    printf '%s [y/N] ' "$1" >/dev/tty
    IFS= read -r reply </dev/tty || reply=""
    case "$reply" in y|Y|yes|Yes|YES) return 0 ;; *) die "stopped; nothing more was changed" ;; esac
}

need() { command -v "$1" >/dev/null 2>&1 || die "$2"; }

# The local SSH public key, its MD5 fingerprint, and the private key next to it.
find_ssh_key() {
    local f
    if [ -z "$SSH_KEY_FILE" ]; then
        for f in ~/.ssh/id_ed25519.pub ~/.ssh/id_ecdsa.pub ~/.ssh/id_rsa.pub; do
            if [ -f "$f" ]; then SSH_KEY_FILE="$f"; break; fi
        done
    fi
    [ -n "$SSH_KEY_FILE" ] && [ -f "$SSH_KEY_FILE" ] \
        || die "no SSH public key found. Make one with: ssh-keygen -t ed25519"
    KEY_FP="$(ssh-keygen -l -E md5 -f "$SSH_KEY_FILE" | awk '{print $2}')"
    KEY_FP="${KEY_FP#MD5:}"
    [ -n "$KEY_FP" ] || die "could not read $SSH_KEY_FILE as an SSH public key"
    KEY_LABEL="phunk-$(printf '%s' "${KEY_FP//:/}" | cut -c1-12)"
    if [ -f "${SSH_KEY_FILE%.pub}" ]; then SSH_OPTS+=(-i "${SSH_KEY_FILE%.pub}"); fi
}

check_disk_size() {
    case "$DISK_GB" in ''|*[!0-9]*) die "DISK_GB must be a number of GB, not '$DISK_GB'" ;; esac
}

# ---------------------------------------------------------------- Hetzner

hetzner_check() {
    need hcloud "the hcloud CLI is not installed. macOS: brew install hcloud. Others: https://github.com/hetznercloud/cli/releases"
    if [ -z "${HCLOUD_TOKEN:-}" ] && [ -z "$(hcloud context active 2>/dev/null || true)" ]; then
        die "no Hetzner API token. Make one (Console -> project -> Security -> API tokens, Read & Write), then: export HCLOUD_TOKEN=<token>"
    fi
    TYPE="${TYPE:-cpx42}" REGION="${REGION:-nbg1}"
}
hetzner_server() { hcloud server describe "$NAME" -o format='{{.ID}}' 2>/dev/null || true; }
hetzner_disk()   { hcloud volume describe "$DISK" -o format='{{.ID}}' 2>/dev/null || true; }
hetzner_plan() {
    PLAN_SERVER="$(hetzner_server)" PLAN_DISK="$(hetzner_disk)"
    DESCRIBE="Hetzner Cloud: server $TYPE (Ubuntu 22.04) in $REGION, volume ${DISK_GB} GB.
Roughly €70 + €100 a month, billed by the hour. Prices: https://www.hetzner.com/cloud"
}
hetzner_create() {
    local key sid vid on
    key="$(hcloud ssh-key list -o noheader -o columns=name,fingerprint | awk -v fp="$KEY_FP" '$2 == fp {print $1; exit}')"
    if [ -z "$key" ]; then
        key="$KEY_LABEL"
        say "Uploading your SSH key ($SSH_KEY_FILE) as $key"
        hcloud ssh-key create --name "$key" --public-key-from-file "$SSH_KEY_FILE" >/dev/null
    fi
    # The server first: if the type is sold out here, nothing is left behind.
    sid="$PLAN_SERVER"
    if [ -z "$sid" ]; then
        say "Creating server $NAME ($TYPE in $REGION)"
        hcloud server create --name "$NAME" --type "$TYPE" --image ubuntu-22.04 \
            --location "$REGION" --ssh-key "$key" >/dev/null \
            || die "could not create the server. If $TYPE is not available in $REGION, try REGION=hel1 or TYPE=ccx23."
        sid="$(hetzner_server)"
        [ -n "$sid" ] || die "the server was not created"
        NEW=1
    else
        say "Server $NAME already exists"
    fi
    vid="$PLAN_DISK"
    if [ -z "$vid" ]; then
        say "Creating volume $DISK (${DISK_GB} GB), attached to $NAME"
        hcloud volume create --name "$DISK" --size "$DISK_GB" --server "$NAME" --format ext4 >/dev/null
        vid="$(hetzner_disk)"
        [ -n "$vid" ] || die "the volume was not created"
    else
        say "Volume $DISK already exists"
        on="$(hcloud volume describe "$DISK" -o format='{{if .Server}}{{.Server.ID}}{{end}}')"
        if [ -z "$on" ]; then
            hcloud volume attach --server "$NAME" "$DISK" >/dev/null
        elif [ "$on" != "$sid" ]; then
            die "volume $DISK is attached to another server (id $on)"
        fi
    fi
    IP="$(hcloud server ip "$NAME")" SSH_USER=root
    DATA_DEV="/dev/disk/by-id/scsi-0HC_Volume_$vid"
}
hetzner_ip() { [ -n "$(hetzner_server)" ] || die "no server named $NAME"; hcloud server ip "$NAME"; }
hetzner_destroy() {
    local sid vid
    sid="$(hetzner_server)" vid="$(hetzner_disk)"
    [ -n "$sid$vid" ] || { echo "Nothing to delete: no server $NAME, no volume $DISK."; return 0; }
    echo "This deletes, for good:"
    [ -z "$sid" ] || echo "  server $NAME ($(hcloud server ip "$NAME" 2>/dev/null || echo '?'))"
    [ -z "$vid" ] || echo "  volume $DISK, with the node's whole database"
    confirm "Delete them?"
    if [ -n "$sid" ]; then say "Deleting server $NAME"; hcloud server delete "$NAME" >/dev/null; fi
    if [ -n "$vid" ]; then say "Deleting volume $DISK"; hcloud volume delete "$DISK" >/dev/null; fi
}

# ----------------------------------------------------------- DigitalOcean

digitalocean_check() {
    need doctl "the doctl CLI is not installed. macOS: brew install doctl. Others: https://docs.digitalocean.com/reference/doctl/how-to/install/"
    doctl account get >/dev/null 2>&1 \
        || die "doctl is not logged in. Make a token (Control Panel -> API -> Generate New Token, read and write), then: export DIGITALOCEAN_ACCESS_TOKEN=<token>"
    TYPE="${TYPE:-s-8vcpu-16gb}" REGION="${REGION:-nyc3}"
}
digitalocean_server() { doctl compute droplet list --format ID,Name --no-header | awk -v n="$NAME" '$2 == n {print $1; exit}'; }
digitalocean_disk()   { doctl compute volume list --format ID,Name --no-header | awk -v n="$DISK" '$2 == n {print $1; exit}'; }
digitalocean_plan() {
    PLAN_SERVER="$(digitalocean_server)" PLAN_DISK="$(digitalocean_disk)"
    DESCRIBE="DigitalOcean: droplet $TYPE (Ubuntu 22.04) in $REGION, volume ${DISK_GB} GiB.
Roughly \$96 + \$200 a month, billed by the hour. Prices: https://www.digitalocean.com/pricing"
}
digitalocean_create() {
    local key sid vid
    key="$(doctl compute ssh-key list --format ID,FingerPrint --no-header | awk -v fp="$KEY_FP" '$2 == fp {print $1; exit}')"
    if [ -z "$key" ]; then
        say "Uploading your SSH key ($SSH_KEY_FILE) as $KEY_LABEL"
        key="$(doctl compute ssh-key import "$KEY_LABEL" --public-key-file "$SSH_KEY_FILE" --format ID --no-header)"
    fi
    sid="$PLAN_SERVER"
    if [ -z "$sid" ]; then
        say "Creating droplet $NAME ($TYPE in $REGION); about a minute"
        sid="$(doctl compute droplet create "$NAME" --image ubuntu-22-04-x64 --size "$TYPE" \
            --region "$REGION" --ssh-keys "$key" --wait --format ID --no-header)" \
            || die "could not create the droplet. If $TYPE is not available in $REGION, try another REGION or TYPE."
        [ -n "$sid" ] || die "the droplet was not created"
        NEW=1
    else
        say "Droplet $NAME already exists"
    fi
    vid="$PLAN_DISK"
    if [ -z "$vid" ]; then
        say "Creating volume $DISK (${DISK_GB} GiB)"
        vid="$(doctl compute volume create "$DISK" --region "$REGION" --size "${DISK_GB}GiB" \
            --fs-type ext4 --format ID --no-header)"
        [ -n "$vid" ] || die "the volume was not created"
    else
        say "Volume $DISK already exists"
    fi
    if ! doctl compute volume get "$vid" --format DropletIDs --no-header | grep -qw "$sid"; then
        say "Attaching $DISK to $NAME"
        doctl compute volume-action attach "$vid" "$sid" --wait >/dev/null
    fi
    IP="$(digitalocean_ip)" SSH_USER=root
    DATA_DEV="/dev/disk/by-id/scsi-0DO_Volume_$DISK"
}
digitalocean_ip() {
    doctl compute droplet list --format Name,PublicIPv4 --no-header | awk -v n="$NAME" '$1 == n {print $2; exit}' \
        | grep . || die "no droplet named $NAME"
}
digitalocean_destroy() {
    local sid vid
    sid="$(digitalocean_server)" vid="$(digitalocean_disk)"
    [ -n "$sid$vid" ] || { echo "Nothing to delete: no droplet $NAME, no volume $DISK."; return 0; }
    echo "This deletes, for good:"
    [ -z "$sid" ] || echo "  droplet $NAME"
    [ -z "$vid" ] || echo "  volume $DISK, with the node's whole database"
    confirm "Delete them?"
    if [ -n "$sid" ]; then
        say "Deleting droplet $NAME"
        doctl compute droplet delete "$sid" --force >/dev/null
        sleep 10   # the volume detaches as the droplet goes
    fi
    if [ -n "$vid" ]; then say "Deleting volume $DISK"; doctl compute volume delete "$vid" --force >/dev/null; fi
}

# -------------------------------------------------------------------- AWS

awsr() { aws --region "$REGION" --output text "$@"; }

aws_check() {
    need aws "the aws CLI is not installed: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"
    REGION="${REGION:-${AWS_REGION:-$(aws configure get region 2>/dev/null || true)}}"
    [ -n "$REGION" ] || die "no AWS region. Set one: export AWS_REGION=us-east-1"
    aws --region "$REGION" sts get-caller-identity >/dev/null 2>&1 \
        || die "the aws CLI is not logged in. Run: aws configure (or set AWS_PROFILE)"
    TYPE="${TYPE:-m6i.xlarge}"
}
aws_server() {
    awsr ec2 describe-instances \
        --filters "Name=tag:Name,Values=$NAME" "Name=instance-state-name,Values=pending,running,stopping,stopped" \
        --query 'Reservations[].Instances[].InstanceId' | awk '{print $1}'
}
aws_sg() {
    local sg
    sg="$(awsr ec2 describe-security-groups --filters "Name=group-name,Values=$NAME-sg" \
        --query 'SecurityGroups[0].GroupId' 2>/dev/null || true)"
    [ "$sg" = None ] || printf '%s' "$sg"
}
aws_plan() {
    PLAN_SERVER="$(aws_server)" PLAN_DISK="$PLAN_SERVER"   # the disk is the instance's own
    DESCRIBE="AWS EC2: instance $TYPE (Ubuntu 22.04) in $REGION, with a ${DISK_GB} GB gp3 disk.
Roughly \$140 + \$160 a month, billed by the second. Prices: https://aws.amazon.com/ec2/pricing/on-demand/"
}
aws_create() {
    local sid sg ami root
    awsr ec2 describe-key-pairs --key-names "$KEY_LABEL" >/dev/null 2>&1 || {
        say "Uploading your SSH key ($SSH_KEY_FILE) as $KEY_LABEL"
        awsr ec2 import-key-pair --key-name "$KEY_LABEL" --public-key-material "fileb://$SSH_KEY_FILE" >/dev/null
    }
    sg="$(aws_sg)"
    if [ -z "$sg" ]; then
        say "Creating security group $NAME-sg (SSH, 3381/tcp, 3382/udp, 3383/tcp)"
        sg="$(awsr ec2 create-security-group --group-name "$NAME-sg" \
            --description "Snapchain read node (phunk-client)" --query GroupId)"
        awsr ec2 authorize-security-group-ingress --group-id "$sg" --ip-permissions \
            'IpProtocol=tcp,FromPort=22,ToPort=22,IpRanges=[{CidrIp=0.0.0.0/0}]' \
            'IpProtocol=tcp,FromPort=3381,ToPort=3381,IpRanges=[{CidrIp=0.0.0.0/0}]' \
            'IpProtocol=udp,FromPort=3382,ToPort=3382,IpRanges=[{CidrIp=0.0.0.0/0}]' \
            'IpProtocol=tcp,FromPort=3383,ToPort=3383,IpRanges=[{CidrIp=0.0.0.0/0}]' >/dev/null
    fi
    sid="$PLAN_SERVER"
    if [ -z "$sid" ]; then
        ami="$(awsr ssm get-parameter --query Parameter.Value \
            --name /aws/service/canonical/ubuntu/server/22.04/stable/current/amd64/hvm/ebs-gp2/ami-id)"
        root="$(awsr ec2 describe-images --image-ids "$ami" --query 'Images[0].RootDeviceName')"
        say "Creating instance $NAME ($TYPE in $REGION, ${DISK_GB} GB disk)"
        sid="$(awsr ec2 run-instances --image-id "$ami" --instance-type "$TYPE" \
            --key-name "$KEY_LABEL" --security-group-ids "$sg" \
            --block-device-mappings "[{\"DeviceName\":\"$root\",\"Ebs\":{\"VolumeSize\":$DISK_GB,\"VolumeType\":\"gp3\",\"DeleteOnTermination\":true}}]" \
            --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$NAME}]" \
            --query 'Instances[0].InstanceId')" \
            || die "could not create the instance. Try another TYPE or REGION."
        NEW=1
    else
        say "Instance $NAME already exists"
    fi
    awsr ec2 wait instance-running --instance-ids "$sid"
    IP="$(aws_ip)" SSH_USER=ubuntu
    DATA_DEV=""   # the data lives on the instance's own (big) disk
}
aws_ip() {
    local ip
    ip="$(awsr ec2 describe-instances \
        --filters "Name=tag:Name,Values=$NAME" "Name=instance-state-name,Values=pending,running" \
        --query 'Reservations[].Instances[].PublicIpAddress' | awk '{print $1}')"
    [ -n "$ip" ] && [ "$ip" != None ] || die "no running instance named $NAME"
    printf '%s\n' "$ip"
}
aws_destroy() {
    local sid sg
    sid="$(aws_server)" sg="$(aws_sg)"
    [ -n "$sid$sg" ] || { echo "Nothing to delete: no instance $NAME, no security group $NAME-sg."; return 0; }
    echo "This deletes, for good:"
    [ -z "$sid" ] || echo "  instance $NAME ($sid), with its disk and the node's whole database"
    [ -z "$sg" ] || echo "  security group $NAME-sg"
    confirm "Delete them?"
    if [ -n "$sid" ]; then
        say "Terminating $NAME (a minute or two)"
        awsr ec2 terminate-instances --instance-ids "$sid" >/dev/null
        awsr ec2 wait instance-terminated --instance-ids "$sid"
    fi
    if [ -n "$sg" ]; then say "Deleting security group $NAME-sg"; awsr ec2 delete-security-group --group-id "$sg" >/dev/null; fi
}

# ------------------------------------------- any server you already have

byo_check() { SSH_USER="${WHERE%@*}" IP="${WHERE#*@}"; }
byo_plan() {
    PLAN_SERVER=x PLAN_DISK=x   # nothing to create
    DESCRIBE=""
}
byo_create() {
    DATA_DEV="${DATA_DEVICE:-}"
    echo
    echo "This prepares $SSH_USER@$IP: updates its packages, installs Docker, creates"
    echo "the $NODE_USER user and turns on its firewall."
    if [ -n "$DATA_DEV" ]; then echo "It puts the node's data on $DATA_DEV (formatted only if blank)."; fi
    confirm "Go on?"
}

# ------------------------------------------------------- shared: prepare

# Run a command on the server as root (through sudo when we log in as a user).
on_server() {
    local sudo=""
    [ "$SSH_USER" = root ] || sudo="sudo "
    # shellcheck disable=SC2029  # meant to expand here
    ssh -n "${SSH_OPTS[@]}" "$SSH_USER@$IP" "$sudo$1"
}

wait_for_ssh() {
    local i
    say "Waiting for SSH on $SSH_USER@$IP"
    for i in $(seq 60); do
        if ssh -n "${SSH_OPTS[@]}" "$SSH_USER@$IP" true 2>/dev/null; then return 0; fi
        [ "$i" -eq 1 ] || printf '.'
        sleep 5
    done
    echo
    die "could not log in to $SSH_USER@$IP over SSH after 5 minutes"
}

prepare() {
    # A new server can get an address someone used before; forget its old host key.
    if [ "$NEW" -eq 1 ]; then ssh-keygen -R "$IP" >/dev/null 2>&1 || true; fi
    wait_for_ssh
    say "Waiting for the server's first boot to finish"
    on_server 'cloud-init status --wait >/dev/null 2>&1 || true'

    say "Preparing the server (provision.sh; a few minutes)"
    local sudo=""
    [ "$SSH_USER" = root ] || sudo="sudo "
    # shellcheck disable=SC2029  # PROVISION_URL is meant to expand here
    ssh -n "${SSH_OPTS[@]}" "$SSH_USER@$IP" "curl -fsSL '$PROVISION_URL' | ${sudo}bash"

    if [ -n "$DATA_DEV" ]; then
        say "Putting the data disk at /home/$NODE_USER/snapchain"
        local sudo_bash="bash"
        [ "$SSH_USER" = root ] || sudo_bash="sudo bash"
        # shellcheck disable=SC2029  # DATA_DEV and NODE_USER are meant to expand here
        ssh "${SSH_OPTS[@]}" "$SSH_USER@$IP" "$sudo_bash -s -- '$DATA_DEV' '$NODE_USER'" <<'REMOTE'
set -euo pipefail
dev="$1" user="$2"
dir="/home/$user/snapchain"
for _ in $(seq 60); do [ -e "$dev" ] && break; sleep 2; done
[ -e "$dev" ] || { echo "error: no disk at $dev" >&2; exit 1; }
blkid "$dev" >/dev/null 2>&1 || mkfs.ext4 -q "$dev"
mkdir -p "$dir"
if ! mountpoint -q "$dir"; then
    if [ -n "$(ls -A "$dir")" ]; then
        echo "error: $dir already has files but no disk on it; move them away first" >&2
        exit 1
    fi
    grep -qF "$dev " /etc/fstab || echo "$dev $dir ext4 discard,nofail,defaults 0 0" >> /etc/fstab
    mount "$dir"
fi
chown "$user:$user" "$dir"
df -h "$dir" | tail -n 1
REMOTE
    fi

    if on_server 'test -f /var/run/reboot-required'; then
        say "Rebooting to finish the updates"
        on_server 'systemctl reboot' >/dev/null 2>&1 || true
        sleep 15
        wait_for_ssh
    fi

    cat <<EOF

Done. The server is at $IP. Next, start the node (command 1):

    ssh -t $NODE_USER@$IP ./node.sh start

Once ./node.sh status says every shard is in sync, read from it (command 2):

    ssh $NODE_USER@$IP
    pip install 'phunk-client[write] @ git+https://github.com/Ramitphi/phunk-client'
    phunk --node http://localhost:3381 whois dwr
EOF
    if [ "$P" != byo ]; then printf '\nDelete everything later with: %s\n' "$(self destroy)"; fi
}

# ------------------------------------------------------------------- main

case "$WHERE" in
    hetzner|digitalocean|aws) P="$WHERE" ;;
    *@*) P=byo ;;
    ""|-h|--help|help)
        sed -n '2,/^$/s/^# \{0,1\}//p' "${BASH_SOURCE[0]:-$0}" 2>/dev/null \
            || echo "usage: server.sh hetzner|digitalocean|aws|user@host [create|ip|destroy]"
        exit 0 ;;
    *) die "unknown provider '$WHERE': use hetzner, digitalocean, aws, or user@host for a server you have" ;;
esac

need ssh "ssh is not installed"
"${P}_check"
check_disk_size

case "$CMD" in
    create)
        # A server you already have may log you in with your usual SSH setup.
        if [ "$P" != byo ] || [ -n "$SSH_KEY_FILE" ]; then find_ssh_key; fi
        "${P}_plan"
        if [ -n "$DESCRIBE" ] && { [ -z "$PLAN_SERVER" ] || [ -z "$PLAN_DISK" ]; }; then
            printf '\nAbout to create (this costs money):\n%s\nDelete it later with: %s\n' "$DESCRIBE" "$(self destroy)"
            confirm "Create it?"
        fi
        "${P}_create"
        prepare
        ;;
    ip)
        [ "$P" != byo ] || { echo "$IP"; exit 0; }
        "${P}_ip" ;;
    destroy)
        [ "$P" != byo ] || die "destroy only works for servers this script rented"
        "${P}_destroy"
        echo "Deleted. Your SSH key stays in your account; remove it there if you like." ;;
    *) die "unknown command '$CMD' (create, ip, destroy)" ;;
esac

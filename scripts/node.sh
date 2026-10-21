#!/usr/bin/env bash
#
# node.sh: install, run and check a Snapchain read node.
#
#   ./node.sh start     install the node if needed, then start it
#   ./node.sh stop      stop the node
#   ./node.sh status    sync progress, per shard, in plain words
#   ./node.sh logs      follow the node's logs (Ctrl-C stops following, not the node)
#   ./node.sh upgrade   fetch the latest release and restart
#
# This is a thin wrapper around Farcaster's official snapchain.sh, the script
# behind the bootstrap one-liner in
# https://snapchain.farcaster.xyz/guides/running-a-node. Like the bootstrap,
# it puts everything in ~/snapchain and runs the node with Docker Compose.
# On top of that it:
#   - asks the two first-run questions up front, or takes them from the
#     environment, so the install runs unattended
#   - keeps Grafana (3000) and statsd (8125/8126) on 127.0.0.1, because
#     ports that Docker publishes skip the ufw firewall
#   - returns once the node is up, instead of following the logs forever
#
# Settings (environment variables):
#   SNAPCHAIN_AGREE_NO_REWARDS=yes  agree that the node earns no rewards
#                                   (asked on first start if not set)
#   SNAPCHAIN_OPERATOR_FID=<fid>    your FID or username; only used to pick the
#                                   weekday for auto-upgrades. Leave empty to
#                                   turn auto-upgrade off.
#   SNAPCHAIN_DIR                   install dir (default: ~/snapchain)
#   SNAPCHAIN_API                   node to check in "status"
#                                   (default: http://localhost:3381)
#
# Needs root or sudo for everything except "status".

set -euo pipefail

SNAPCHAIN_RAW="https://raw.githubusercontent.com/farcasterxyz/snapchain/@latest"
NODE_DIR="${SNAPCHAIN_DIR:-$HOME/snapchain}"
API="${SNAPCHAIN_API:-http://localhost:3381}"
OVERRIDE_MARK="written by phunk-client scripts/node.sh"
COMPOSE_MIN="2.24.4"   # first Compose release with the !override tag
STALL_DELAY=$((30 * 86400))   # a shard this far behind is syncing from block 1

say()  { printf '==> %s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die()  { printf 'error: %s\n' "$*" >&2; exit 1; }

as_root() {
    if [ "$(id -u)" -eq 0 ]; then "$@"; else sudo "$@"; fi
}

have_tty() { { : </dev/tty; } 2>/dev/null; }

ask() {
    printf '%s' "$1" >/dev/tty
    IFS= read -r REPLY </dev/tty || REPLY=""
}

installed() {
    [ -x "$NODE_DIR/snapchain.sh" ] && [ -f "$NODE_DIR/docker-compose.yml" ]
}

# Run docker compose in the node dir. Uses plain docker when this user can
# reach it (docker group), sudo otherwise.
compose() {
    local pre=()
    if [ "$(id -u)" -ne 0 ] && ! docker info >/dev/null 2>&1; then
        pre=(sudo -n)
    fi
    (cd "$NODE_DIR" && "${pre[@]}" docker compose "$@")
}

# --- first-run setup -------------------------------------------------------

ensure_jq() {
    command -v jq >/dev/null 2>&1 && return 0
    command -v apt-get >/dev/null 2>&1 || die "install jq first"
    say "Installing jq"
    as_root apt-get update -q
    as_root apt-get install -y -q jq
}

fetch_snapchain_sh() {
    [ -x "$NODE_DIR/snapchain.sh" ] && return 0
    say "Downloading the official snapchain.sh into $NODE_DIR"
    local tmp
    tmp="$(mktemp)"
    curl -fsSL "$SNAPCHAIN_RAW/scripts/snapchain.sh?t=$(date +%s)" -o "$tmp" \
        || { rm -f "$tmp"; die "could not download snapchain.sh"; }
    as_root install -m 0755 "$tmp" "$NODE_DIR/snapchain.sh"
    rm -f "$tmp"
}

env_has() { grep -q "^$1=" "$NODE_DIR/.env" 2>/dev/null; }
env_get() { grep "^$1=" "$NODE_DIR/.env" 2>/dev/null | tail -n 1 | cut -d= -f2-; }
env_add() { printf '%s=%s\n' "$1" "$2" | as_root tee -a "$NODE_DIR/.env" >/dev/null; }

# Turn a FID or username into a FID. Prints nothing if it can't.
resolve_fid() {
    local input="$1"
    case "$input" in
        '') return 0 ;;
        *[!0-9]*)
            curl -fsS --max-time 10 "https://fnames.farcaster.xyz/transfers?name=$input" 2>/dev/null \
                | jq -r '.transfers[-1].to // empty' 2>/dev/null || true ;;
        *) printf '%s\n' "$input" ;;
    esac
}

# Answer the questions snapchain.sh would ask, so it can run unattended.
ensure_env() {
    as_root touch "$NODE_DIR/.env"

    if ! grep -q '^AGREE_NO_REWARDS_FOR_ME=true' "$NODE_DIR/.env"; then
        local answer="${SNAPCHAIN_AGREE_NO_REWARDS:-}"
        if [ -z "$answer" ]; then
            have_tty || die "set SNAPCHAIN_AGREE_NO_REWARDS=yes to agree that the node earns no rewards"
            echo "IMPORTANT: you will NOT get any rewards for running this node."
            ask '> Type "Yes" to continue: '
            answer="$REPLY"
        fi
        case "$(printf '%s' "$answer" | tr '[:upper:]' '[:lower:]')" in
            yes|y|true) env_add AGREE_NO_REWARDS_FOR_ME true ;;
            *) die "you need to agree to run a node" ;;
        esac
    fi

    if ! env_has HUB_OPERATOR_FID; then
        local input="${SNAPCHAIN_OPERATOR_FID:-}" fid
        if [ -z "$input" ] && have_tty; then
            echo "Your FID is only used to pick the weekday for weekly auto-upgrades."
            ask '> Your FID or Farcaster username (Enter to skip): '
            input="$REPLY"
        fi
        fid="$(resolve_fid "$input")"
        if [ -n "$fid" ]; then
            env_add HUB_OPERATOR_FID "$fid"
        else
            [ -z "$input" ] || warn "could not find '$input'"
            env_add HUB_OPERATOR_FID 0
        fi
    fi

    # snapchain.sh stops before starting the node if it has no FID to pick an
    # upgrade day from, so turn auto-upgrade off in that case.
    if [ "$(env_get HUB_OPERATOR_FID)" = "0" ] && ! env_has SKIP_CRONTAB; then
        env_add SKIP_CRONTAB true
        echo "Auto-upgrade is off. Run ./node.sh upgrade every week or so."
    fi
}

compose_new_enough() {
    local v
    v="$(docker compose version --short 2>/dev/null || true)"
    v="${v#v}"
    [ -n "$v" ] || return 0   # not installed yet; snapchain.sh installs a current one
    [ "$(printf '%s\n' "$COMPOSE_MIN" "$v" | sort -V | head -n 1)" = "$COMPOSE_MIN" ]
}

# Keep the dashboard and metrics ports off the internet. snapchain.sh never
# touches docker-compose.override.yml, and Compose merges it automatically.
write_override() {
    local f="$NODE_DIR/docker-compose.override.yml"
    if [ -f "$f" ]; then
        grep -qF "$OVERRIDE_MARK" "$f" \
            || warn "$f is yours, leaving it alone; check that port 3000 is not public"
        return 0
    fi
    if ! compose_new_enough; then
        warn "Docker Compose is older than $COMPOSE_MIN; Grafana stays on 0.0.0.0:3000. Block it at your provider's firewall."
        return 0
    fi
    as_root tee "$f" >/dev/null <<EOF
# $OVERRIDE_MARK.
# Docker-published ports skip ufw, so bind the local-only services to
# 127.0.0.1. Reach Grafana with: ssh -L 3000:localhost:3000 <user>@<server>
services:
  grafana:
    ports: !override
      - "127.0.0.1:3000:3000"
  statsd:
    ports: !override
      - "127.0.0.1:8125:8125/udp"
      - "127.0.0.1:8126:8126"
EOF
}

prepare() {
    mkdir -p "$NODE_DIR" 2>/dev/null || as_root mkdir -p "$NODE_DIR"
    as_root true   # ask for a sudo password now, if one is needed
    ensure_jq
    fetch_snapchain_sh
    ensure_env
    write_override
}

# Print the official upgrade's output until it says it is done. It then
# follows the logs forever; leaving the pipe ends that.
until_done() {
    local line
    while IFS= read -r line; do
        printf '%s\n' "$line"
        [ "$line" = "✅ Upgrade complete." ] && return 0
    done
    return 1
}

run_official_upgrade() {
    say "Running the official snapchain.sh upgrade (pulls images; a few minutes)"
    set +o pipefail
    if (cd "$NODE_DIR" && as_root ./snapchain.sh upgrade </dev/null 2>&1) | until_done; then
        set -o pipefail
        return 0
    fi
    set -o pipefail
    die "snapchain.sh upgrade did not finish; see its output above"
}

print_next() {
    cat <<EOF

The node is running. First it downloads a snapshot of several hundred GB,
which takes a few hours. Then it syncs the blocks made since.
Don't restart or upgrade the node until the snapshot download finishes.

  ./node.sh status   sync progress
  ./node.sh logs     follow the logs (Ctrl-C stops following; the node keeps running)

When every shard is in sync, read from it:

  Hub("http://<this-server's-public-ip>:3381")
EOF
}

# --- commands --------------------------------------------------------------

cmd_start() {
    prepare
    if [ -f "$NODE_DIR/docker-compose.yml" ]; then
        say "Starting the node"
        compose up -d snapchain statsd grafana || die "docker compose up failed"
    else
        run_official_upgrade
    fi
    print_next
}

cmd_upgrade() {
    prepare
    run_official_upgrade
    print_next
}

cmd_stop() {
    if ! installed; then
        echo "No node installed in $NODE_DIR."
        return 0
    fi
    (cd "$NODE_DIR" && as_root ./snapchain.sh down)
}

cmd_logs() {
    installed || die "no node installed in $NODE_DIR; run ./node.sh start"
    cd "$NODE_DIR" && as_root ./snapchain.sh logs
}

human_secs() {
    local s="$1"
    if   [ "$s" -lt 60 ];    then printf '%ss' "$s"
    elif [ "$s" -lt 3600 ];  then printf '%sm %ss' $((s / 60)) $((s % 60))
    elif [ "$s" -lt 86400 ]; then printf '%sh %sm' $((s / 3600)) $((s % 3600 / 60))
    else                          printf '%sd %sh' $((s / 86400)) $((s % 86400 / 3600))
    fi
}

# "0" -> "0", "0 2" -> "0 and 2", "0 1 2" -> "0, 1 and 2"
and_list() {
    local out="$1"
    shift
    if [ "$#" -eq 0 ]; then printf '%s' "$out"; return 0; fi
    while [ "$#" -gt 1 ]; do out="$out, $1"; shift; done
    printf '%s and %s' "$out" "$1"
}

# Some shards are syncing from block 1 because their snapshot download was
# cut short. Say so, and how to fix it. Never deletes anything itself.
explain_stalled() {
    local which it
    if [ "$#" -eq 1 ]; then
        which="Shard $1 is"; it="it"
    else
        which="Shards $(and_list "$@") are"; it="them"
    fi
    cat <<EOF
$which far behind the others. The snapshot download for $it was
interrupted, probably by a restart, so the node is syncing $it from block 1.
Waiting would take months. Download the snapshot again instead (a few hours):

  ./node.sh stop
  sudo rm -rf $NODE_DIR/.rocks $NODE_DIR/.rocks.snapshot
  ./node.sh start

This deletes the node's data; the snapshot brings it back. Don't restart
or upgrade the node until the download finishes.
EOF
}

# The API is not up yet. Say why, from the container state and its logs.
explain_down() {
    if ! installed; then
        echo "No node installed in $NODE_DIR. Run ./node.sh start."
        return 0
    fi
    local state
    if ! state="$(compose ps -a --format '{{.State}}' snapchain 2>/dev/null)"; then
        echo "Can't reach Docker as this user. Try: sudo ./node.sh status"
        return 0
    fi
    if [ -z "$state" ]; then
        echo "The node's container does not exist. Run ./node.sh start."
        return 0
    fi
    if [ "$state" != "running" ]; then
        echo "The node's container is $state. Run ./node.sh start, or ./node.sh logs to see why."
        return 0
    fi

    local logs meta chunk total shard size
    logs="$(compose logs --no-color --tail 500 snapchain 2>/dev/null || true)"
    meta="$(printf '%s\n' "$logs" | grep -o 'Retrieving metadata from [^ ]*' | tail -n 1 | awk '{print $4}' || true)"
    chunk="$(printf '%s\n' "$logs" | grep -o 'Downloading zipped snapshot chunk chunk_[0-9]*' | tail -n 1 | grep -o '[0-9]*$' || true)"
    if [ -n "$meta" ]; then
        shard="$(printf '%s\n' "$meta" | awk -F/ '{print $(NF-1)}')"
        total="$(curl -fsS --max-time 10 "$meta" 2>/dev/null | jq -r '.chunks | length' 2>/dev/null || true)"
        if [ -n "$chunk" ] && [ -n "$total" ]; then
            echo "Downloading the snapshot: shard $shard, chunk $((10#$chunk)) of $total."
        elif [ -n "$chunk" ]; then
            echo "Downloading the snapshot: shard $shard, chunk $((10#$chunk))."
        else
            echo "Downloading the snapshot (shard $shard)."
        fi
        size="$(du -sh "$NODE_DIR/.rocks.snapshot" "$NODE_DIR/.rocks" 2>/dev/null | awk '{printf "%s%s %s", sep, $1, $2; sep=", "}' || true)"
        [ -z "$size" ] || echo "On disk so far: $size"
        echo "This takes a few hours. The API comes up when it is done."
    else
        echo "The container is running but the API is not up yet. Last log lines:"
        printf '%s\n' "$logs" | tail -n 5
    fi
}

cmd_status() {
    command -v jq >/dev/null 2>&1 || die "status needs jq (sudo apt-get install -y jq)"
    local info
    if ! info="$(curl -fsS --max-time 10 "$API/v1/info" 2>/dev/null)"; then
        echo "The node's API at $API is not answering."
        case "$API" in
            *://localhost:*|*://127.0.0.1:*) explain_down ;;
        esac
        return 1
    fi

    local version msgs fids bytes next
    read -r version msgs fids bytes next < <(printf '%s' "$info" | jq -r '[
        (.version // "?"),
        (.dbStats.numMessages // 0),
        (.dbStats.numFidRegistrations // 0),
        (.dbStats.approxSize // 0),
        (.nextEngineVersionTimestamp // 0)
    ] | map(tostring) | join(" ")')

    echo "Snapchain $version at $API"
    printf '  %s messages, %s fids, %s GB on disk\n\n' \
        "$msgs" "$fids" "$(awk -v b="$bytes" 'BEGIN { printf "%.0f", b / 1e9 }')"

    # The highest shard height, to spot a shard that is far below the rest.
    local top
    top="$(printf '%s' "$info" | jq -r '[.shardInfos[].maxHeight // 0] | max // 0')"

    local id height delay word behind lagging=0 stalled=()
    while read -r id height delay; do
        if   [ "$delay" -lt 10 ];  then word="in sync"
        elif [ "$delay" -lt 120 ]; then word="nearly in sync"
        elif [ "$delay" -gt "$STALL_DELAY" ] && [ $((height * 2)) -lt "$top" ]; then
            word="snapshot interrupted"; lagging=1; stalled+=("$id")
        else                            word="catching up"; lagging=1
        fi
        behind="$(human_secs "$delay") behind"
        printf '  shard %s  height %-10s %-14s %s\n' "$id" "$height" "$behind" "$word"
    done < <(printf '%s' "$info" | jq -r '.shardInfos | sort_by(.shardId)[] |
        "\(.shardId) \(.maxHeight // 0) \(.blockDelay // 0)"')
    echo

    local now
    now="$(date +%s)"
    if [ "$next" -gt "$now" ] 2>/dev/null; then
        echo "A protocol upgrade is due on $(date -u -d "@$next" '+%Y-%m-%d %H:%M UTC')."
        echo "Run ./node.sh upgrade before then."
        echo
    fi

    if [ "${#stalled[@]}" -gt 0 ]; then
        explain_stalled "${stalled[@]}"
        return 2
    fi
    if [ "$lagging" -eq 1 ]; then
        echo "Still catching up. Reads work, but recent messages may be missing."
        return 2
    fi
    echo "In sync."
    case "$API" in
        *://localhost:*|*://127.0.0.1:*)
            echo "Point a client at http://<this-server's-public-ip>:3381" ;;
    esac
}

usage() {
    cat <<'EOF'
usage: node.sh <command>

  start     install the node if needed, then start it
  stop      stop the node
  status    sync progress, per shard (exit 0 in sync, 2 catching up, 1 down)
  logs      follow the node's logs (Ctrl-C stops following, not the node)
  upgrade   fetch the latest release and restart
EOF
}

case "${1:-}" in
    start)   cmd_start ;;
    stop)    cmd_stop ;;
    status)  cmd_status ;;
    logs)    cmd_logs ;;
    upgrade) cmd_upgrade ;;
    help|-h|--help|'') usage ;;
    *) usage >&2; exit 64 ;;
esac

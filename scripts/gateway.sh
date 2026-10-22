#!/usr/bin/env bash
#
# gateway.sh: put your Snapchain node behind an HTTPS gateway that only
# your own apps can use.
#
#   sudo NODE_DOMAIN=node.example.com ./gateway.sh setup
#   sudo ./gateway.sh token add phunk-social    print a new token for one app
#   sudo ./gateway.sh token remove phunk-social
#   sudo ./gateway.sh token list                names only; tokens are never shown again
#   sudo ./gateway.sh status                    is it up, does it refuse strangers
#
# A node's API is open to anyone who finds it, and Docker publishes it past
# ufw. This puts nginx in front of it, on 443:
#   - every request needs "Authorization: Bearer <token>"; one token per
#     app, so you can see who uses what and cut one off without the others
#   - a rate limit per token (GATEWAY_RATE), so one app, or a leaked token,
#     can't take the whole node
#   - only the node's /v1/ reads, plus submitMessage and validateMessage
#   - gzip, which makes the JSON pages several times smaller
#   - a short cache (GATEWAY_CACHE_SECS) shared by every app, and identical
#     requests in flight at once go to the node only once
#   - keep-alive connections to the node
#
# Then close the node's own ports with "./node.sh private" (as the node's
# user), so the gateway is the only way in. Gossip (3382/udp) stays open:
# the node syncs over it.
#
# Settings (environment variables; setup saves them in
# /etc/snapchain-gateway/env, so later runs keep them):
#   NODE_DOMAIN         a DNS name with an A record for this server (required)
#   CERT_EMAIL          for Let's Encrypt expiry mail (optional)
#   GATEWAY_RATE        requests per second per token (default 300)
#   GATEWAY_BURST       requests over the rate a token may queue (default 600)
#   GATEWAY_CONNS       connections at once per token (default 256)
#   GATEWAY_CACHE_SECS  seconds a read stays cached (default 15; 0 turns it off)
#   NODE_UPSTREAM       the node's HTTP API (default 127.0.0.1:3381)
#
# Safe to run again. Needs root, and ports 80 and 443 free for nginx.

set -euo pipefail

CONF_DIR=/etc/snapchain-gateway
ENV_FILE="$CONF_DIR/env"
TOKENS="$CONF_DIR/tokens.map"
SITE=/etc/nginx/sites-available/snapchain-gateway
WEBROOT=/var/www/letsencrypt
CACHE_DIR=/var/cache/nginx/snapchain

say()  { printf '==> %s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die()  { printf 'error: %s\n' "$*" >&2; exit 1; }

need_root() { [ "$(id -u)" -eq 0 ] || die "run this as root (sudo $0 $*)"; }

# Settings: the environment wins, then the saved file, then the defaults.
load_settings() {
    local saved_domain="" saved_email="" saved_rate="" saved_burst="" saved_conns="" saved_cache="" saved_up=""
    if [ -f "$ENV_FILE" ]; then
        saved_domain="$(saved NODE_DOMAIN)"
        saved_email="$(saved CERT_EMAIL)"
        saved_rate="$(saved GATEWAY_RATE)"
        saved_burst="$(saved GATEWAY_BURST)"
        saved_conns="$(saved GATEWAY_CONNS)"
        saved_cache="$(saved GATEWAY_CACHE_SECS)"
        saved_up="$(saved NODE_UPSTREAM)"
    fi
    NODE_DOMAIN="${NODE_DOMAIN:-$saved_domain}"
    CERT_EMAIL="${CERT_EMAIL:-$saved_email}"
    GATEWAY_RATE="${GATEWAY_RATE:-${saved_rate:-300}}"
    GATEWAY_BURST="${GATEWAY_BURST:-${saved_burst:-600}}"
    GATEWAY_CONNS="${GATEWAY_CONNS:-${saved_conns:-256}}"
    GATEWAY_CACHE_SECS="${GATEWAY_CACHE_SECS:-${saved_cache:-15}}"
    NODE_UPSTREAM="${NODE_UPSTREAM:-${saved_up:-127.0.0.1:3381}}"
}

saved() { grep "^$1=" "$ENV_FILE" 2>/dev/null | tail -n 1 | cut -d= -f2- || true; }

check_settings() {
    [ -n "$NODE_DOMAIN" ] || die "set NODE_DOMAIN to a name that points at this server, e.g. sudo NODE_DOMAIN=node.example.com $0 setup"
    case "$NODE_DOMAIN" in
        *[!a-zA-Z0-9.-]*|.*|*.) die "NODE_DOMAIN '$NODE_DOMAIN' doesn't look like a DNS name" ;;
    esac
    case "$CERT_EMAIL" in
        ''|*@*.*) ;;
        *) die "CERT_EMAIL '$CERT_EMAIL' doesn't look like an email address" ;;
    esac
    case "$CERT_EMAIL" in *[!a-zA-Z0-9@._+-]*) die "CERT_EMAIL has odd characters" ;; esac
    local n
    for n in "$GATEWAY_RATE" "$GATEWAY_BURST" "$GATEWAY_CONNS" "$GATEWAY_CACHE_SECS"; do
        case "$n" in ''|*[!0-9]*) die "GATEWAY_* settings are whole numbers, got '$n'" ;; esac
    done
    [ "$GATEWAY_RATE" -gt 0 ] || die "GATEWAY_RATE must be above 0"
    case "$NODE_UPSTREAM" in
        *[!a-zA-Z0-9.:-]*) die "NODE_UPSTREAM is host:port, got '$NODE_UPSTREAM'" ;;
    esac
}

save_settings() {
    install -d -m 0755 "$CONF_DIR"
    umask 077
    cat >"$ENV_FILE.tmp" <<EOF
NODE_DOMAIN=$NODE_DOMAIN
CERT_EMAIL=$CERT_EMAIL
GATEWAY_RATE=$GATEWAY_RATE
GATEWAY_BURST=$GATEWAY_BURST
GATEWAY_CONNS=$GATEWAY_CONNS
GATEWAY_CACHE_SECS=$GATEWAY_CACHE_SECS
NODE_UPSTREAM=$NODE_UPSTREAM
EOF
    mv "$ENV_FILE.tmp" "$ENV_FILE"
    umask 022
}

install_packages() {
    if command -v nginx >/dev/null 2>&1 && command -v certbot >/dev/null 2>&1; then
        return 0
    fi
    command -v apt-get >/dev/null 2>&1 || die "install nginx and certbot first"
    say "Installing nginx and certbot"
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -q
    apt-get install -y -q nginx certbot
}

ensure_tokens_file() {
    install -d -m 0755 "$CONF_DIR"
    if [ ! -f "$TOKENS" ]; then
        install -m 0600 /dev/null "$TOKENS"
    fi
    chmod 0600 "$TOKENS"
}

cert_dir() { printf '/etc/letsencrypt/live/%s' "$NODE_DOMAIN"; }
have_cert() { [ -s "$(cert_dir)/fullchain.pem" ] && [ -s "$(cert_dir)/privkey.pem" ]; }

# The nginx config. Before there is a certificate, only port 80 (for the
# Let's Encrypt challenge); after, the gateway on 443 too.
write_site() {
    local tmp cache_on="" v6=""
    tmp="$(mktemp)"
    [ "$GATEWAY_CACHE_SECS" -gt 0 ] && cache_on=1
    # Listen on IPv6 too, when this machine has it.
    [ -s /proc/net/if_inet6 ] && v6=1
    cat >"$tmp" <<EOF
# Written by phunk-client scripts/gateway.sh; run it again instead of editing.

# Token -> app name. Unknown or missing tokens map to "" and get a 401.
# "Bearer " and 64 hex digits is longer than the default bucket of 64.
map_hash_bucket_size 128;
map \$http_authorization \$snap_client {
    default "";
    include $TOKENS;
}

limit_req_zone \$snap_client zone=snap_read:10m rate=${GATEWAY_RATE}r/s;
limit_req_zone \$snap_client zone=snap_write:10m rate=10r/s;
limit_conn_zone \$snap_client zone=snap_conns:10m;

log_format snap '\$remote_addr \$snap_client [\$time_local] "\$request_method \$uri" '
                '\$status \$body_bytes_sent \$request_time \$upstream_cache_status';

upstream snapchain_api {
    server $NODE_UPSTREAM;
    keepalive 64;
}
EOF
    if [ -n "$cache_on" ]; then
        cat >>"$tmp" <<EOF

proxy_cache_path $CACHE_DIR levels=1:2 keys_zone=snapchain:20m max_size=2g inactive=10m use_temp_path=off;
EOF
    fi
    cat >>"$tmp" <<EOF

server {
    listen 80;
${v6:+    listen [::]:80;
}    server_name $NODE_DOMAIN;

    location /.well-known/acme-challenge/ {
        root $WEBROOT;
    }
    location / {
        return 301 https://\$host\$request_uri;
    }
}
EOF
    if have_cert; then
        cat >>"$tmp" <<EOF

server {
    listen 443 ssl;
${v6:+    listen [::]:443 ssl;
}    server_name $NODE_DOMAIN;

    ssl_certificate $(cert_dir)/fullchain.pem;
    ssl_certificate_key $(cert_dir)/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_cache shared:snap_tls:10m;
    ssl_session_timeout 1d;

    # The request line, never the Authorization header.
    access_log /var/log/nginx/snapchain-gateway.log snap;

    keepalive_requests 10000;
    keepalive_timeout 75s;
    client_max_body_size 64k;

    gzip on;
    gzip_types application/json;
    gzip_proxied any;
    gzip_min_length 1024;
    gzip_comp_level 4;
    gzip_vary on;

    limit_req_status 429;
    limit_conn_status 429;

    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_set_header Authorization "";
    proxy_set_header Host \$host;
    proxy_connect_timeout 5s;
    proxy_read_timeout 60s;

    # Writes: a signed message goes to the network. Low rate, never cached.
    location ~ ^/v1/(submitMessage|validateMessage)\$ {
        if (\$snap_client = "") {
            return 401;
        }
        limit_except POST {
            deny all;
        }
        limit_req zone=snap_write burst=20 nodelay;
        limit_conn snap_conns $GATEWAY_CONNS;
        proxy_pass http://snapchain_api;
    }

    # Reads.
    location /v1/ {
        if (\$snap_client = "") {
            return 401;
        }
        limit_except GET {
            deny all;
        }
        limit_req zone=snap_read burst=$GATEWAY_BURST nodelay;
        limit_conn snap_conns $GATEWAY_CONNS;
EOF
        if [ -n "$cache_on" ]; then
            cat >>"$tmp" <<EOF
        # Same answer whatever the token, so the apps share one cache.
        proxy_cache snapchain;
        proxy_cache_key \$request_uri;
        proxy_cache_valid 200 404 ${GATEWAY_CACHE_SECS}s;
        proxy_cache_lock on;
        proxy_cache_lock_timeout 10s;
        proxy_cache_use_stale updating error timeout http_502 http_503 http_504;
        proxy_cache_background_update on;
        proxy_ignore_headers Cache-Control Expires Set-Cookie;
        add_header X-Cache \$upstream_cache_status always;
EOF
        fi
        cat >>"$tmp" <<EOF
        proxy_pass http://snapchain_api;
    }

    location / {
        return 404;
    }
}
EOF
    fi
    install -m 0644 "$tmp" "$SITE"
    rm -f "$tmp"
    ln -sf "$SITE" /etc/nginx/sites-enabled/snapchain-gateway
}

reload_nginx() {
    nginx -t -q || die "nginx refused the config (nginx -t shows why)"
    if systemctl is-active --quiet nginx; then
        systemctl reload nginx
    else
        systemctl enable --now nginx >/dev/null
    fi
}

get_cert() {
    have_cert && return 0
    say "Getting a certificate for $NODE_DOMAIN from Let's Encrypt"
    local email=(--register-unsafely-without-email)
    [ -z "$CERT_EMAIL" ] || email=(--email "$CERT_EMAIL")
    certbot certonly --webroot -w "$WEBROOT" -d "$NODE_DOMAIN" \
        --non-interactive --agree-tos "${email[@]}" \
        --deploy-hook "systemctl reload nginx" \
        || die "no certificate. Check that $NODE_DOMAIN has an A record for this server and that port 80 is open."
}

open_firewall() {
    command -v ufw >/dev/null 2>&1 || return 0
    ufw allow 80/tcp comment 'HTTP (certificates)' >/dev/null
    ufw allow 443/tcp comment 'Snapchain gateway' >/dev/null
    # The node's API ports no longer need to be open. (Docker skips ufw for
    # them anyway: ./node.sh private is what closes them.)
    ufw delete allow 3381/tcp >/dev/null 2>&1 || true
    ufw delete allow 3383/tcp >/dev/null 2>&1 || true
}

cmd_setup() {
    need_root setup
    load_settings
    check_settings
    install_packages
    save_settings
    ensure_tokens_file
    install -d -m 0755 "$WEBROOT"
    install -d -m 0700 -o www-data "$CACHE_DIR" 2>/dev/null || install -d -m 0700 "$CACHE_DIR"
    # Ubuntu's default site also listens on 80; ours names the domain, so it
    # wins for that name. Leave the default alone.
    write_site
    open_firewall
    reload_nginx
    get_cert
    write_site
    reload_nginx
    say "The gateway is up at https://$NODE_DOMAIN"
    if [ ! -s "$TOKENS" ]; then
        echo
        echo "No tokens yet, so it refuses everyone. Make one per app:"
        echo "  sudo $0 token add phunk-social"
    fi
    cat <<EOF

Then, as the node's user, close the node's own ports so this is the only way in:
  ./node.sh private
EOF
}

valid_name() {
    case "$1" in
        ''|*[!a-z0-9-]*) die "token names are lowercase letters, digits and dashes, like phunk-social" ;;
    esac
}

# Replace the tokens file with $1 and reload nginx. If nginx refuses the
# result, put the old file back, so a bad edit never takes the gateway down.
commit_tokens() {
    local new="$1" old
    old="$(mktemp)"
    cp -p "$TOKENS" "$old"
    install -m 0600 "$new" "$TOKENS"
    rm -f "$new"
    if [ -f "$SITE" ] && ! nginx -t -q 2>/dev/null; then
        install -m 0600 "$old" "$TOKENS"
        rm -f "$old"
        die "nginx refused the new tokens file; left it as it was"
    fi
    rm -f "$old"
    [ ! -f "$SITE" ] || reload_nginx
}

cmd_token() {
    need_root token
    local action="${1:-}" name="${2:-}"
    load_settings
    ensure_tokens_file
    case "$action" in
        add)
            valid_name "$name"
            grep -q " $name;\$" "$TOKENS" && die "there is already a token named $name; remove it first"
            command -v openssl >/dev/null 2>&1 || die "install openssl first"
            local token tmp
            token="$(openssl rand -hex 32)"
            tmp="$(mktemp)"
            cat "$TOKENS" >"$tmp"
            printf '"Bearer %s" %s;\n' "$token" "$name" >>"$tmp"
            commit_tokens "$tmp"
            cat <<EOF
Token for $name (shown once; put it in that app's settings, never in a repo):

  $token

For phunk.social, in Vercel: PHUNK_NODE=https://${NODE_DOMAIN:-<NODE_DOMAIN>}  PHUNK_NODE_TOKEN=<the token>
EOF
            ;;
        remove)
            valid_name "$name"
            grep -q " $name;\$" "$TOKENS" || die "no token named $name"
            local tmp
            tmp="$(mktemp)"
            grep -v " $name;\$" "$TOKENS" >"$tmp" || true
            commit_tokens "$tmp"
            echo "Removed $name. Requests with its token now get a 401."
            ;;
        list)
            if [ -s "$TOKENS" ]; then
                awk '{ sub(/;$/, "", $3); print $3 }' "$TOKENS"
            else
                echo "No tokens."
            fi
            ;;
        *) die "usage: $0 token add|remove|list [name]" ;;
    esac
}

cmd_status() {
    load_settings
    [ -n "$NODE_DOMAIN" ] || die "not set up yet: sudo NODE_DOMAIN=... $0 setup"
    local url="https://$NODE_DOMAIN/v1/info" code ok=0
    if systemctl is-active --quiet nginx 2>/dev/null; then
        echo "nginx is running."
    else
        echo "nginx is not running."; ok=1
    fi
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$url" || true)"
    case "$code" in
        401) echo "Without a token: 401, as it should be." ;;
        200) echo "Without a token: 200. It lets anyone in; run sudo $0 setup again."; ok=1 ;;
        *)   echo "Without a token: HTTP $code from $url (is it up? does DNS point here?)"; ok=1 ;;
    esac
    if [ -r "$TOKENS" ] && [ -s "$TOKENS" ]; then
        local first name
        first="$(head -n 1 "$TOKENS")"
        name="$(printf '%s' "$first" | awk '{ sub(/;$/, "", $3); print $3 }')"
        code="$(printf '%s' "$first" | awk '{ gsub(/"/, ""); print "header = \"Authorization: Bearer " $2 "\"" }' \
            | curl -s -o /dev/null -w '%{http_code}' --max-time 10 -K - "$url" || true)"
        if [ "$code" = 200 ]; then
            echo "With the $name token: 200."
        else
            echo "With the $name token: HTTP $code. Is the node up? (./node.sh status)"; ok=1
        fi
        echo "Tokens: $(awk '{ sub(/;$/, "", $3); printf "%s%s", sep, $3; sep=", " }' "$TOKENS")"
    else
        echo "No tokens yet: sudo $0 token add <app-name>"
    fi
    # Docker publishes the node's ports past ufw, so ask Docker.
    local ports
    if ports="$(docker ps --format '{{.Ports}}' 2>/dev/null)"; then
        if printf '%s\n' "$ports" | grep -qE '(0\.0\.0\.0|::):3381->'; then
            echo "The node's own API (3381) is still open to the internet. As the node's user: ./node.sh private"
            ok=1
        elif printf '%s\n' "$ports" | grep -q '127\.0\.0\.1:3381->'; then
            echo "The node's own API (3381) answers on this machine only."
        else
            echo "No container publishes 3381. Is the node running? (./node.sh status)"
        fi
    fi
    return "$ok"
}

usage() {
    cat <<EOF
usage: $0 <command>

  setup                 install nginx, get a certificate, write the gateway (needs NODE_DOMAIN)
  token add <name>      a new token for one app, shown once
  token remove <name>   cut that app off
  token list            the names
  status                is it up, does it refuse requests without a token
EOF
}

case "${1:-}" in
    setup)  cmd_setup ;;
    token)  shift; cmd_token "$@" ;;
    status) cmd_status ;;
    help|-h|--help|'') usage ;;
    *) usage >&2; exit 64 ;;
esac

"""scripts/node.sh upgrade and rollback, offline: docker, curl and crontab
are stand-ins on PATH that log what they're asked and answer from a state
file, so no node, Docker or network is needed."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
NODE_SH = ROOT / "scripts" / "node.sh"

pytestmark = pytest.mark.skipif(not (shutil.which("bash") and shutil.which("jq")), reason="needs bash and jq")

FAKES = {
    # The node's API answers while state is "up" (in sync), "slow" (behind
    # and stuck) or "catching" (behind, rising); not at all while "down".
    "curl": r'''#!/bin/bash
echo "curl $*" >> "$FAKE/log"
state="$(cat "$FAKE/state")"
case "$*" in
  */v1/info*)
    case "$state" in
      up) echo '{"shardInfos":[{"shardId":0,"maxHeight":100,"blockDelay":2},{"shardId":1,"maxHeight":200,"blockDelay":3}]}' ;;
      slow) echo '{"shardInfos":[{"shardId":0,"maxHeight":100,"blockDelay":900}]}' ;;
      catching) n=$(( $(cat "$FAKE/height" 2>/dev/null || echo 100) + 5 )); echo "$n" > "$FAKE/height"
                echo "{\"shardInfos\":[{\"shardId\":0,\"maxHeight\":$n,\"blockDelay\":900}]}" ;;
      *) exit 7 ;;
    esac ;;
  *) exit 7 ;;
esac
''',
    "docker": r'''#!/bin/bash
echo "docker $*" >> "$FAKE/log"
case "$1 $2" in
  "info "*) exit 0 ;;
  "compose version") echo 2.29.0 ;;
  "compose ps") echo cid123 ;;
  "inspect "*) echo sha256:oldimage ;;
  "tag "*) echo "$3" > "$FAKE/tagged" ;;
  "image inspect") [ -f "$FAKE/tagged" ] ;;
  "compose up")
    case "$*" in *"--pull never"*) echo up > "$FAKE/state" ;; esac ;;
esac
''',
    "crontab": r'''#!/bin/bash
if [ "$1" = "-l" ]; then cat "$FAKE/crontab" 2>/dev/null; else cat > "$FAKE/crontab"; fi
''',
    "sleep": "#!/bin/bash\nexit 0\n",
}

# The official script, as far as node.sh uses it: "upgrade" swaps the node to
# the new version (whatever $FAKE/after says) and prints its done line.
SNAPCHAIN_SH = r'''#!/bin/bash
echo "snapchain.sh $*" >> "$FAKE/log"
if [ "$1" = upgrade ]; then
  cat "$FAKE/after" > "$FAKE/state"
  echo "image: farcasterxyz/snapchain:latest (new)" > "$(dirname "$0")/docker-compose.yml"
  echo "✅ Upgrade complete."
  sleep 1000
fi
'''


@pytest.fixture
def node(tmp_path):
    fake, bin_, node_dir = tmp_path / "fake", tmp_path / "bin", tmp_path / "snapchain"
    for d in (fake, bin_, node_dir):
        d.mkdir()
    for name, body in FAKES.items():
        (bin_ / name).write_text(body)
        (bin_ / name).chmod(0o755)
    (node_dir / "snapchain.sh").write_text(SNAPCHAIN_SH)
    (node_dir / "snapchain.sh").chmod(0o755)
    (node_dir / "docker-compose.yml").write_text("image: farcasterxyz/snapchain:latest (old)\n")
    (node_dir / ".env").write_text("AGREE_NO_REWARDS_FOR_ME=true\nHUB_OPERATOR_FID=3\n")
    (fake / "state").write_text("up")
    (fake / "crontab").write_text("5 4 * * * /root/backup.sh\n0 4 * * 2 /root/snapchain/snapchain.sh autoupgrade >> x.log 2>&1\n")

    def run(cmd, after="up", **env):
        (fake / "after").write_text(after)
        e = dict(os.environ, PATH=f"{bin_}:{os.environ['PATH']}", FAKE=str(fake), SNAPCHAIN_DIR=str(node_dir),
                 SNAPCHAIN_API="http://localhost:3381", **env)
        p = subprocess.run(["bash", str(NODE_SH), cmd], env=e, capture_output=True, text=True, timeout=60,
                           stdin=subprocess.DEVNULL)
        return p.returncode, p.stdout + p.stderr

    return run, fake, node_dir


def test_upgrade_turns_off_the_cron_and_keeps_a_rollback_point(node):
    run, fake, node_dir = node
    code, out = run("upgrade")
    assert code == 0, out
    assert "Upgrade done" in out
    # The weekly auto-upgrade is gone; the other cron line stays.
    assert (fake / "crontab").read_text() == "5 4 * * * /root/backup.sh\n"
    assert "SKIP_CRONTAB=true" in (node_dir / ".env").read_text()
    # The running image was kept, with the compose file from before.
    assert (fake / "tagged").read_text().strip() == "phunk/snapchain-rollback:previous"
    assert "(old)" in (node_dir / "rollback" / "docker-compose.yml").read_text()
    assert "(new)" in (node_dir / "docker-compose.yml").read_text()
    assert "PHUNK_PINNED_IMAGE=phunk" not in (node_dir / ".env").read_text()


def test_upgrade_that_catches_up_slowly_is_not_rolled_back(node):
    run, fake, node_dir = node
    code, out = run("upgrade", after="catching")
    assert code == 0, out
    assert "Rolling back" not in out


def test_upgrade_that_never_comes_up_is_rolled_back_and_pinned(node):
    run, fake, node_dir = node
    code, out = run("upgrade", after="down", SNAPCHAIN_UPGRADE_WAIT="1")
    assert code == 3, out
    assert "Rolled back" in out
    override = (node_dir / "docker-compose.override.yml").read_text()
    assert "image: phunk/snapchain-rollback:previous" in override and "pull_policy: never" in override
    assert "(old)" in (node_dir / "docker-compose.yml").read_text()
    assert "PHUNK_PINNED_IMAGE=phunk/snapchain-rollback:previous" in (node_dir / ".env").read_text()
    assert "compose up -d --pull never snapchain" in (fake / "log").read_text()

    # The next upgrade lifts the pin before it pulls.
    code, out = run("upgrade")
    assert code == 0, out
    assert "image: phunk" not in (node_dir / "docker-compose.override.yml").read_text()


def test_upgrade_stuck_far_behind_is_rolled_back(node):
    run, fake, node_dir = node
    code, out = run("upgrade", after="slow", SNAPCHAIN_UPGRADE_WAIT="1")
    assert code == 3, out


def test_a_failed_snapshot_command_stops_the_upgrade(node):
    run, fake, node_dir = node
    code, out = run("upgrade", SNAPCHAIN_SNAPSHOT_CMD="false")
    assert code != 0 and "nothing was upgraded" in out
    assert "snapchain.sh upgrade" not in (fake / "log").read_text()


def test_private_and_pinned_override_is_one_snapchain_block(node):
    run, fake, node_dir = node
    with open(node_dir / ".env", "a") as f:
        f.write("PHUNK_API_PRIVATE=true\n")
    code, out = run("upgrade", after="down", SNAPCHAIN_UPGRADE_WAIT="1")
    assert code == 3, out
    override = (node_dir / "docker-compose.override.yml").read_text()
    assert override.count("  snapchain:") == 1
    block = override.split("  snapchain:")[1]
    assert "image: phunk/snapchain-rollback:previous" in block and '"127.0.0.1:3381:3381/tcp"' in block


def test_start_does_not_upgrade(node):
    run, fake, node_dir = node
    code, out = run("start")
    assert code == 0, out
    assert "compose up -d --pull missing snapchain statsd grafana" in (fake / "log").read_text()

---
title: Read nodes and validators
summary: Validators make blocks. Read nodes follow along and serve the API. Both accept writes, but a read node only passes them on.
order: 5
---

Every Snapchain node runs the same program. One config flag, `read_node`, decides its role.

## Validators

A **validator** proposes blocks and votes on them. Only nodes whose keys are in the validator set can do this. Mainnet has seven, equally weighted. A block is final once validators with more than two thirds of the weight sign it, which today means five of the seven.

## Read nodes

A **read node** follows the chain without voting. It fetches decided blocks from peers, checks their signatures against the validator set, and applies them. It ends up with the same state as a validator, a moment later. Anyone can run one, and it serves the full HTTP and gRPC APIs.

That is the node in the [quickstart](../index.html#quickstart). It needs 16 GB of RAM, 4 CPU cores, 2 TB of disk and ports 3381 to 3383 open.

Hypersnap is a compatible build that serves the same API. The public node this site uses, `haatz.quilibrium.com`, runs it.

## Writes go through read nodes too

You can submit a message to a read node. It does not keep it, though.

1. The read node routes the message to its shard and checks it against that shard's state: valid signature, live key, not already merged.
2. If it passes, the node gossips it to the network on the `mempool` topic and keeps nothing.
3. A validator picks it up from gossip and puts it in a block.

So a success response from a read node means *valid and passed on*, not *in a block*. If the gossip is lost, the message is gone and you need to submit it again. To confirm, read the message back by its hash, or watch the event stream for its `MERGE_MESSAGE` event.

```sh
curl -s "$NODE/v1/castById?fid=3&hash=0x0256ae66308d546514fa666cda44052abbfea066"
```

Submitting straight to a validator puts the message in that validator's own mempool as well. Most people don't have that option, and for normal traffic the read node path works.

## Telling them apart

| | Validator | Read node |
|---|---|---|
| Who can run it | Holders of a key in the validator set | Anyone |
| Consensus | Proposes and votes | Never votes; applies decided blocks |
| Submitted messages | Kept in its mempool, put in blocks | Checked, then relayed to validators |
| `mempoolSize` in `/v1/info` | A real count | `4294967295` on shards 1 and 2, meaning "not reported" |
| Restarting it | Removes a vote; one at a time | Safe at any time |

## Is my node in sync?

```sh
curl -s localhost:3381/v1/info
```

Look at `blockDelay` for each shard. It is how many seconds that shard's newest block is behind the clock. It counts down while the node catches up. Once every shard is in single digits, you're in sync.

Source: [Validators vs. read nodes](https://snapchain.farcaster.xyz/node-types) in the Snapchain docs.

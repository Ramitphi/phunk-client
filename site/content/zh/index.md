---
title: 快速开始
html_title: phunk-client：用你自己的节点读取 Farcaster
summary: 运行一个 Snapchain 读取节点，再用一个小巧的 Python 客户端从它读取 Farcaster。三条命令，然后是教程。
lang: zh-CN
home: yes
alt_href: ../index.html
alt_label: English
alt_lang: en
---

::: hero
# 用你自己的节点读取 Farcaster

Farcaster 上的每一条 cast（帖子）、每一次关注、每一份个人资料，都存放在一个叫 Snapchain 的节点网络里。自己运行一个节点，就能通过普通的 HTTP 读取全部数据。不需要 API 密钥，不需要索引服务，也不需要付费套餐。
:::

::: note 关于语言
本页是快速开始的中文版。详细的教程和概念页面目前只有英文版，下面的链接会打开英文页面。
:::

## 快速开始

三条命令：租一台服务器，在上面启动节点，再从节点读取数据。

::: quickstart
::: step
### 租一台服务器

```sh
# 在你自己的电脑上运行，需要 hcloud 命令行工具，并把 Hetzner API token 放在 HCLOUD_TOKEN 里
curl -fsSL https://raw.githubusercontent.com/Ramitphi/phunk-client/main/scripts/hetzner.sh | bash
```

这条命令会在 Hetzner Cloud 上租一台服务器（8 核 CPU、16 GB 内存），外加一块 2 TB 的数据卷，然后把它配置好：安装 Docker，创建一个带有你 SSH 公钥的 `snapchain` 用户，并打开防火墙。花钱之前它会先问你，最后打印出服务器的 IP 地址。Hetzner 按小时计费，运行 `hetzner.sh destroy` 会删除全部资源。运行之前：安装 `hcloud`（macOS 上用 `brew install hcloud`），在 Hetzner Console → 你的项目 → Security → API tokens 创建一个 Read & Write 的 token，然后 `export HCLOUD_TOKEN=<token>`。具体步骤、费用，以及如何改用其他云服务商，见英文教程的 [Run your own node](/tutorial.html#9-run-your-own-node) 一节。
:::
::: step
### 启动读取节点

```sh
ssh -t snapchain@<服务器IP> ./node.sh start      # 之后用 ./node.sh status 查看同步进度
```

这一步用 Docker 安装 Snapchain，下载一份全网快照（几百 GB，需要几个小时），然后同步快照之后产生的区块。读取节点（read node）跟随链的进度并提供 API，但不参与区块投票。两者的区别见 [Read nodes and validators](/concepts/nodes.html)。

第一次启动时会问两个问题：是否同意运行节点没有奖励（输入 `Yes`），以及你的 FID（直接回车跳过即可）。快照下载期间不要重启节点，否则没下载完的分片会从第 1 个区块开始同步，那要花好几个月。
:::
::: step
### 从节点读取

```sh
pip install 'phunk-client[write] @ git+https://github.com/Ramitphi/phunk-client' && phunk --node http://localhost:3381 whois dwr
```

等 `./node.sh status` 显示每个分片都已同步（in sync）之后再运行这一步。这条命令安装 `phunk-client`，并从你的节点读取 dwr 的个人资料。可以登录服务器（`ssh snapchain@<服务器IP>`）后运行，也可以在自己电脑上运行，把 `localhost` 换成服务器的地址。

要写入数据，先给一个**测试账号**添加应用密钥（app key），再通过同一个节点发帖：

```sh
export PHUNK_NODE=http://localhost:3381   # 之后的 phunk 命令都通过你的节点
phunk key add
phunk post "hello from my own node"
```

`key add` 会向你要这个账号的助记词（seed phrase），只用一次，之后只保存新生成的应用密钥。添加应用密钥不需要 gas，也不需要链上交易。写入的消息先交给你的读取节点，节点检查无误后转发给验证者节点（validator），由它们打包进区块。
:::
:::

::: warning 助记词很危险
助记词就是整个账号。拿到它的人可以转走账号、改掉一切。跟着本教程测试时，**只用测试账号**，不要用你的主账号。一般来说，只把助记词粘贴到你信任的开发者写的软件里；能用委托签名授权（delegated signer authorization）的时候就用它，这样应用根本看不到你的助记词。详见 [Seed phrases are dangerous](/concepts/seed-phrases.html)。
:::

::: note 还没有服务器？
跳过前两步。公共节点提供同样的 API，客户端默认就用它。三条命令：

```sh
pip install git+https://github.com/Ramitphi/phunk-client
phunk whois dwr
phunk feed dwr --hide warpcast
```
:::

## 写入：关注、点赞、按客户端筛选

添加应用密钥之后，同一个客户端可以读取信息流、发帖、关注和点赞，还能只看或隐藏某个客户端发出的 cast：

```sh
phunk follow dwr
phunk feed                       # 每条 cast 下面一行是它的 FID:HASH
phunk like <FID:HASH>            # 从信息流里复制一个
phunk feed --only warpcast       # 只看某个客户端发的 cast
phunk feed --hide warpcast       # 隐藏某个客户端发的 cast
```

每条消息都由一把密钥签名，而每把密钥都记录着是哪个应用申请的，所以客户端能看出每条 cast 是用哪个应用发的。

## 关键概念

| 概念 | 一句话解释 |
|---|---|
| FID | 每个账号的数字编号，比如 dwr 是 3。用户名可以改，FID 不变。 |
| 托管地址（custody address） | 拥有这个 FID 的以太坊地址，由助记词控制。它能添加或撤销应用密钥，也能转移账号。 |
| 应用密钥（app key） | 一把 Ed25519 密钥，替账号签名 cast、点赞和关注。权限比托管地址小，可以随时撤销，也会自动过期。现在添加它不需要 gas。 |
| Snapchain 消息 | 一条签名过的 protobuf 消息：一条 cast、一次点赞、一次关注都是一条消息。时间戳从 2021-01-01 起算，不是 1970 年。 |
| 读取节点与验证者 | 验证者节点出块并投票；读取节点跟随链、保存完整数据、提供 API，并把收到的写入转发给验证者。 |
| 存储租金（storage rent） | 账号按年租用存储空间，才能在网络上保存消息。空间满了，同类消息里最旧的一条会被删掉。 |

每个概念都有一页简短的英文说明，见 [Concepts](/concepts/index.html)。

## 深入学习（英文）

<ul class="cards">
<li><a href="../tutorial.html"><strong>Tutorial：从节点读取 Farcaster</strong><span>从一条 curl 命令一直到找出每条 cast 是哪个应用签名的，再到运行自己的节点。阅读大约 30 分钟。</span></a></li>
<li><a href="../writing.html"><strong>Writing to the network：写入网络</strong><span>添加无需 gas 的应用密钥，然后通过自己的节点发帖、点赞、关注。</span></a></li>
<li><a href="../concepts/index.html"><strong>Concepts：概念</strong><span>FID、托管地址、应用密钥、消息、节点和存储租金，每个一页。</span></a></li>
<li><a href="../concepts/seed-phrases.html"><strong>Seed phrases are dangerous：助记词很危险</strong><span>在任何脚本向你要助记词之前，先读这一页。</span></a></li>
</ul>

## 客户端做了什么

`phunk-client` 是一个小巧的异步 Python 库。读取只依赖 `aiohttp`；写入另外需要几个可选的签名库。它封装了节点的 HTTP API，并处理那些容易出错的地方：

- **跨分片翻页。** 有些查询会把每个分片各一页排好序的结果拼在一起返回，而且翻页标记可能循环。客户端按消息哈希去重，并能干净地停下来。
- **Farcaster 时间。** 消息时间戳从 2021-01-01 开始计秒。每个模型都有一个 UTC 的 `.time`。
- **是哪个应用签的名。** 每条消息都注明签名用的密钥，客户端顺着这把密钥找到申请它的应用。
- **写入。** 无需 gas 的应用密钥、cast、点赞和关注，签名结果与官方 TypeScript 库逐字节一致。
- **有礼貌。** 限速、限制同时进行的请求数，失败时退避重试。

```python
import asyncio
from phunk import Hub

async def main():
    async with Hub("http://localhost:3381") as hub:
        fid = await hub.fid_by_name("dwr")
        async for cast in hub.casts(fid, limit=5):
            print(cast.time, cast.text[:60])

asyncio.run(main())
```

不传 URL 时，默认使用公共节点 `https://haatz.quilibrium.com`。

# PriceGuard

Multi-source price oracle for GenLayer. Validators independently fetch the registered
sources, and consensus is reached on a **canonical price**: the median (integer micro-USD)
rounded to 4 significant digits. Validators require **exact equality** of that canonical
value, and only that value is stored, so the stored oracle price is exactly what validators
bound. Invalid data never becomes a price.

## Consensus design (v3)
- v2 let validators accept a leader price "within tolerance" of their own, while the leader's
  raw price was stored. Two materially different prices could both pass consensus.
- v3: leader and validators both compute `canon(median)` and the validator accepts only on
  `leader.price == mine.price` (no tolerance). Storage, history and the circuit breaker use
  only this canonical value. The per-source count is no longer taken from the leader; the
  stored `sources` is the configured `min_sources`, a lower bound every validator enforced.
- `max_dev_bps` is only used to reject rounds where the sources disagree with each other; it
  plays no role in validator agreement.
- Trade-off (fails safe): if validators land on opposite sides of a bucket edge the round is
  rejected. This costs liveness, never correctness: a wrong price is never written.
- Canonical bucket example: 84848.00 -> 84850.00 (about 0.01% granularity at BTC prices).

## Deployment
- Network: GenLayer Bradbury Testnet (chain ID 4221)
- Contract (v3, the submission): `0x88e58aef1f074121c69B196607D51323CDb758AD`
- Explorer: https://explorer-bradbury.genlayer.com/address/0x88e58aef1f074121c69B196607D51323CDb758AD
- Deployment tx: `0x6e869dc29cdcde6d230400a2396b589564117eebda81cbf067290b3dd0ce81c4`
- Previous v2 contract (superseded, not the submission): `0x50A6Af0F70426F7c94c44e147A505315aFdC8855`

## Oracle controls
- Up to 3 sources per asset, median price, reject if a source deviates more than `max_dev_bps`
- Host allowlist (https only), managed by the owner
- Exact-equality consensus on a canonical price (see Consensus design)
- Source timestamp freshness check (`max_age_sec`)
- Normalization to integer micro-USD, rejects zero, negative and non-finite values
- Price-jump circuit breaker (`max_jump_bps`), owner can `resetPrice`
- Minimum interval between updates
- Failed rounds never write a price; the reason is readable via `getLastError`
- Ownership is set once in the constructor

## Usage (genlayer CLI)
SRC='json:[{"url":"https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd&include_last_updated_at=true","path":"bitcoin.usd","ts_path":"bitcoin.last_updated_at"}]'
genlayer write CONTRACT registerAsset --args BTC "$SRC" 1 300 200 3000
genlayer write CONTRACT updatePrice --args BTC
genlayer call CONTRACT getPrice --args BTC
genlayer call CONTRACT getLastError --args BTC
The `json:` prefix is needed because the CLI turns arguments starting with `[` into arrays.

## Evidence, v2 contract (superseded)
- Valid update, BTC = 84848.000000 USD: `0x13c2d5ee82542d5b9ea24037f6aff62360f60cb750dc5bae5d42490759cf77aa`
- Invalid source rejected, no price stored: `0xcdec68bce49e20d8cc7d295c67898b4f902e2323f67c26b187a5ba0b9547093d`

## Test
pip install genvm-linter genlayer-test
genvm-lint check PriceGuard.py
genvm-lint typecheck PriceGuard.py
pytest -q # 24 tests

## Recommended multi-source setup (keyless exchanges)
Free aggregator APIs can rate-limit or block validators (we observed HTTP 429/403 from
CoinGecko and HTTP 401 from CryptoCompare on Bradbury). The contract rejects such rounds
instead of storing bad data. Three keyless exchange sources worked on Bradbury:
SRC3='json:[{"url":"https://api.coinbase.com/v2/prices/BTC-USD/spot","path":"data.amount","ts_path":""},{"url":"https://api.kraken.com/0/public/Ticker?pair=XBTUSD","path":"result.XXBTZUSD.c.0","ts_path":""},{"url":"https://www.bitstamp.net/api/v2/ticker/btcusd/","path":"last","ts_path":""}]'
genlayer write CONTRACT addAllowedHost --args api.coinbase.com # also api.kraken.com, www.bitstamp.net
genlayer write CONTRACT registerAsset --args BTC3 "$SRC3" 2 300 200 3000
genlayer write CONTRACT updatePrice --args BTC3

## Multi-source evidence, v2 contract (superseded)
- 3-source update, median stored, `sources=3`: `0xa5acaabc4b35d9faff70ac8e153e17b9b4428bc48543efef06c8d99b4daa20b5`
- Host not on the allowlist is rejected: `0xc958ff0b64190f433855533331ae940eebe5ba7586f423fca478db7f14b8056b`
- `min_sources` enforced (one source returned HTTP 401, no price stored): `0x4df6c39b15398bab05dba3ea7656276913e94045991b206be7e8689f0618c539`

v2 note: these hashes belong to the superseded v2 contract. See "v3 evidence" below for the
submitted contract.

## v3 evidence (Bradbury)
Contract: `0x88e58aef1f074121c69B196607D51323CDb758AD`

Every transaction below is `ACCEPTED` / `FINISHED_WITH_RETURN`, and in each round all 5 validators
voted AGREE with the same `validatorResultHash` (exact agreement on the canonical result).

| Case | Tx | Observed result |
|---|---|---|
| 3-source update (BTC3, min_sources=2) | `0xe109f5a1eb541d671f831ca6f0bd7cb34fd0dc49f0cad8061de7b3feb2c32fbd` | `getPrice`: price_micro `81850000000` (canonical, 4 significant digits), sources `2` (= min_sources) |
| 3 sources, tight deviation limit (BTCDEV, max_dev_bps=1) | `0xc6425a5a0069513e03e361fc9c3ccd308a61ae1c7efbc861dc95bde111cebf99` | status ok (exchanges were within 0.01% of each other) |
| min_sources not met (BTCMIN, one source has a wrong path) | `0x5b8cadf096d72696fbc025828438d498e7f408ebc9a32e5cefed8310acec81a7` | status invalid, `getLastError`: "too few valid sources", no price stored |
| Sources disagree (BTCDIV, synthetic) | `0xeb980f5d2f700b61c81fcb526d84e67272b8e1b83eb6c2433b5a3cf4bc7064de` | status invalid, `getLastError`: "sources disagree", `getPrice` returns "Price not found" |
| Host not allowlisted (EVIL1) | `0xc268322177efdee9e18b51119784bbc7caae3c47dbb242fc11c94aad040d7276` | registration rejected, `getAsset` returns `{}` |

Asset registration txs: BTCDEV `0xf31006890562125583eb0a1f02d467975803e92cd37638cb848d7d55b9e98472`,
BTCMIN `0x89c12450b7ca6938212aeb8dd58487637f5e8b1564f9303b7e424415915725d3`,
BTCDIV `0x865bbbaf572deac04d67a77481aed4b62a06c8acc26cbc637e5eab71e26a5731`.

Notes and limits:
- BTCDIV is a synthetic test: its two "sources" are the BTC and ETH spot endpoints of the same
  exchange, used only to force a real cross-source disagreement on-chain.
- The bucket-edge case (validators landing on opposite sides of a canonical bucket) is covered by
  local tests (`test_bucket_boundary_fails_safe`), not demonstrated on-chain.
- The LLM fallback (empty `path`) is still untested.
- The text stored in `getLastError` is diagnostic only; it never influences a stored price.
- The genlayer CLI sometimes reports a successfully processed write as "reverted". This evidence
  only lists transactions whose receipt shows ACCEPTED, plus state read back with view calls.

# PriceGuard

Multi-source price oracle for GenLayer. Validators independently fetch the registered
sources, and consensus is reached on the normalized price (integer micro-USD) within a
tolerance. Invalid data never becomes a price.

## Deployment
- Network: GenLayer Bradbury Testnet (chain ID 4221)
- Contract: `0x50A6Af0F70426F7c94c44e147A505315aFdC8855`
- Explorer: https://explorer-bradbury.genlayer.com/address/0x50A6Af0F70426F7c94c44e147A505315aFdC8855
- Deployment tx: `0x2eab5756ce1e78f6bc8158127a100a3d667799e48140c940aebdb08c487afdce`

## Oracle controls
- Up to 3 sources per asset, median price, reject if a source deviates more than `max_dev_bps`
- Host allowlist (https only), managed by the owner
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

## Evidence (Bradbury)
- Valid update, BTC = 84848.000000 USD: `0x13c2d5ee82542d5b9ea24037f6aff62360f60cb750dc5bae5d42490759cf77aa`
- Invalid source rejected, no price stored: `0xcdec68bce49e20d8cc7d295c67898b4f902e2323f67c26b187a5ba0b9547093d`

## Test
pip install genvm-linter genlayer-test
genvm-lint check PriceGuard.py
genvm-lint typecheck PriceGuard.py
pytest -q # 18 tests

## Recommended multi-source setup (keyless exchanges)
Free aggregator APIs can rate-limit or block validators (we observed HTTP 429/403 from
CoinGecko and HTTP 401 from CryptoCompare on Bradbury). The contract rejects such rounds
instead of storing bad data. Three keyless exchange sources worked on Bradbury:
SRC3='json:[{"url":"https://api.coinbase.com/v2/prices/BTC-USD/spot","path":"data.amount","ts_path":""},{"url":"https://api.kraken.com/0/public/Ticker?pair=XBTUSD","path":"result.XXBTZUSD.c.0","ts_path":""},{"url":"https://www.bitstamp.net/api/v2/ticker/btcusd/","path":"last","ts_path":""}]'
genlayer write CONTRACT addAllowedHost --args api.coinbase.com # also api.kraken.com, www.bitstamp.net
genlayer write CONTRACT registerAsset --args BTC3 "$SRC3" 2 300 200 3000
genlayer write CONTRACT updatePrice --args BTC3

## Multi-source evidence (Bradbury)
- 3-source update, median stored, `sources=3`: `0xa5acaabc4b35d9faff70ac8e153e17b9b4428bc48543efef06c8d99b4daa20b5`
- Host not on the allowlist is rejected: `0xc958ff0b64190f433855533331ae940eebe5ba7586f423fca478db7f14b8056b`
- `min_sources` enforced (one source returned HTTP 401, no price stored): `0x4df6c39b15398bab05dba3ea7656276913e94045991b206be7e8689f0618c539`

Not covered on-chain: the max-deviation rejection (covered by local tests) and the LLM
fallback (`path` empty), which has no tests yet.

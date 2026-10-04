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

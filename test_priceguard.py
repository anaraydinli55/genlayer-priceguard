import json, datetime
import pytest

CG = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd&include_last_updated_at=true"
BN = "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"

def ts(iso):
    return int(datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())

T0 = "2026-10-03T12:00:00Z"

def set_time(vm, iso):
    """warp() does not refresh gl.message_raw in direct mode, so set both."""
    import sys
    vm.warp(iso)
    sys.modules["genlayer.gl"].message_raw["datetime"] = iso

def cg_body(price, when=T0):
    return json.dumps({"bitcoin": {"usd": price, "last_updated_at": ts(when)}})

def mock(vm, pattern, body, status=200):
    vm.mock_web(pattern, {"method": "GET", "status": status, "body": body})

SRC_CG = [{"url": CG, "path": "bitcoin.usd", "ts_path": "bitcoin.last_updated_at"}]
SRC_TWO = SRC_CG + [{"url": BN, "path": "price", "ts_path": ""}]

@pytest.fixture
def pg(direct_vm, direct_deploy):
    direct_vm.warp(T0)
    c = direct_deploy("PriceGuard.py")
    return c

def reg(pg, sources=SRC_CG, min_sources=1, dev=200, jump=3000):
    pg.registerAsset("BTC", json.dumps(sources), min_sources, 300, dev, jump)

def test_owner_only_register(pg, direct_vm, direct_bob):
    with direct_vm.prank(direct_bob):
        with direct_vm.expect_revert("Only owner"):
            reg(pg)

def test_host_allowlist(pg, direct_vm):
    bad = [{"url": "https://evil.example.com/x", "path": "a", "ts_path": ""}]
    with direct_vm.expect_revert("Host not allowlisted"):
        reg(pg, bad)
    with direct_vm.expect_revert("https"):
        reg(pg, [{"url": "http://api.coingecko.com/x", "path": "a", "ts_path": ""}])

def test_update_ok_and_validator(pg, direct_vm):
    reg(pg)
    mock(direct_vm, r"coingecko", cg_body(65000.5))
    pg.updatePrice("BTC")
    p = pg.getPrice("BTC")
    assert p["price"] == "65000.500000" and p["sources"] == "1"
    assert direct_vm.run_validator() is True
    # validator sees a slightly different price (within 2%) -> agree
    direct_vm.clear_mocks(); mock(direct_vm, r"coingecko", cg_body(65100.0))
    assert direct_vm.run_validator() is True
    # validator sees a very different price -> disagree
    direct_vm.clear_mocks(); mock(direct_vm, r"coingecko", cg_body(70000.0))
    assert direct_vm.run_validator() is False

def test_http_error_is_invalid_not_price(pg, direct_vm):
    reg(pg)
    mock(direct_vm, r"coingecko", "oops", status=500)
    pg.updatePrice("BTC")
    with direct_vm.expect_revert("Price not found"):
        pg.getPrice("BTC")
    assert "too few valid sources" in pg.getLastError("BTC")["reason"]

@pytest.mark.parametrize("body", ["not json", json.dumps({"bitcoin": {"usd": -5, "last_updated_at": ts(T0)}}),
                                  json.dumps({"bitcoin": {"usd": 0, "last_updated_at": ts(T0)}}),
                                  json.dumps({"bitcoin": {"usd": "abc", "last_updated_at": ts(T0)}}),
                                  json.dumps({"x": 1})])
def test_garbage_rejected(pg, direct_vm, body):
    reg(pg)
    mock(direct_vm, r"coingecko", body)
    pg.updatePrice("BTC")
    assert pg.getLastError("BTC")
    with direct_vm.expect_revert("Price not found"):
        pg.getPrice("BTC")

def test_stale_rejected(pg, direct_vm):
    reg(pg)
    mock(direct_vm, r"coingecko", cg_body(65000, "2026-10-03T11:00:00Z"))  # 1h old, max 300s
    pg.updatePrice("BTC")
    assert "stale" in pg.getLastError("BTC")["reason"]

def test_two_sources_median_and_disagreement(pg, direct_vm):
    reg(pg, SRC_TWO, min_sources=2)
    mock(direct_vm, r"coingecko", cg_body(65000))
    mock(direct_vm, r"binance", json.dumps({"price": "65100"}))
    pg.updatePrice("BTC")
    assert pg.getPrice("BTC")["sources"] == "2"
    direct_vm.clear_mocks()
    set_time(direct_vm, "2026-10-03T12:01:00Z")
    mock(direct_vm, r"coingecko", cg_body(65000, "2026-10-03T12:01:00Z"))
    mock(direct_vm, r"binance", json.dumps({"price": "80000"}))   # >2% apart
    pg.updatePrice("BTC")
    assert pg.getLastError("BTC")["reason"] == "sources disagree"
    assert pg.getPrice("BTC")["price"] == "65100.000000"  # old price kept (median of 65000, 65100)

def test_min_sources_enforced(pg, direct_vm):
    reg(pg, SRC_TWO, min_sources=2)
    mock(direct_vm, r"coingecko", cg_body(65000))
    mock(direct_vm, r"binance", "x", status=503)
    pg.updatePrice("BTC")
    assert "too few valid sources" in pg.getLastError("BTC")["reason"]

def test_circuit_breaker_and_reset(pg, direct_vm):
    reg(pg, jump=1000)  # 10%
    mock(direct_vm, r"coingecko", cg_body(100))
    pg.updatePrice("BTC")
    set_time(direct_vm, "2026-10-03T12:01:00Z"); direct_vm.clear_mocks()
    mock(direct_vm, r"coingecko", cg_body(150, "2026-10-03T12:01:00Z"))
    pg.updatePrice("BTC")
    assert "circuit breaker" in pg.getLastError("BTC")["reason"]
    assert pg.getPrice("BTC")["price"] == "100.000000"
    pg.resetPrice("BTC")
    set_time(direct_vm, "2026-10-03T12:02:00Z"); direct_vm.clear_mocks()
    mock(direct_vm, r"coingecko", cg_body(150, "2026-10-03T12:02:00Z"))
    pg.updatePrice("BTC")
    assert pg.getPrice("BTC")["price"] == "150.000000"

def test_rate_limit(pg, direct_vm):
    reg(pg)
    mock(direct_vm, r"coingecko", cg_body(100))
    pg.updatePrice("BTC")
    with direct_vm.expect_revert("too recently"):
        pg.updatePrice("BTC")

def test_history_capped(pg, direct_vm):
    reg(pg, jump=10000)
    base = ts(T0)
    for i in range(55):
        t = datetime.datetime.fromtimestamp(base + 60 * i, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        set_time(direct_vm, t); direct_vm.clear_mocks()
        mock(direct_vm, r"coingecko", cg_body(100 + i, t))
        pg.updatePrice("BTC")
    assert len(pg.getPriceHistory("BTC")) == 50

def test_unknown_asset(pg, direct_vm):
    with direct_vm.expect_revert("Asset not found"):
        pg.updatePrice("NOPE")


def test_register_accepts_cli_json_prefix(pg):
    pg.registerAsset("ETH", "json:" + json.dumps(SRC_CG), 1, 300, 200, 3000)
    assert pg.getAsset("ETH")["symbol"] == "ETH"


def test_header_is_pure_json():
    """GenVM joins ALL leading '#' lines and parses them as one JSON document.
    Any extra comment line there makes deploy fail with 'invalid_contract'."""
    lines = open("PriceGuard.py").read().split("\n")
    block = []
    for line in lines:
        if line.startswith("#"):
            block.append(line[1:].strip() if line.startswith("# ") else line[1:])
        else:
            break
    parsed = json.loads("".join(block))
    assert list(parsed.keys()) == ["Depends"]

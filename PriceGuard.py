# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

import json

from genlayer import *

"""
PriceGuard v2 - multi-source price oracle with validator-verified consensus.
Flow of updatePrice(symbol):
  leader   : fetches every registered source, extracts a USD price
             (JSON path, or LLM fallback when the path is empty), checks
             freshness, normalises to integer micro-USD, takes the median
             and rejects the round if the sources disagree.
  validator: independently re-runs the same collection and agrees only if
             the status matches and the prices are within tolerance.
  result   : "ok"      -> price + history are stored
             "invalid" -> price is NOT touched, the reason is stored in
                          last_error so bad data can never become a price.
"""

ERROR_EXPECTED = "[EXPECTED]"
ERROR_LLM = "[LLM_ERROR]"

MICRO = 1_000_000            # prices are stored as integer micro-USD
HISTORY_LIMIT = 50
MAX_SOURCES = 3
MIN_UPDATE_INTERVAL = 30     # seconds between two updates of one asset
DEFAULT_HOSTS = [
    "api.coingecko.com",
    "api.binance.com",
    "min-api.cryptocompare.com",
]


# ---------------------------------------------------------------- helpers

def _sender():
    msg = gl.message
    acct = getattr(msg, "sender_address", None)
    if acct is None:
        acct = msg.sender_account
    return acct


def _tx_time() -> int:
    """Unix seconds of the current transaction (0 if unavailable)."""
    try:
        import datetime

        raw = str(gl.message_raw["datetime"])
        main = raw.replace("Z", "").split("+")[0]
        date_part, _, time_part = main.partition("T")
        year, month, day = (int(x) for x in date_part.split("-"))
        hms = time_part.split(".")[0]
        hour, minute, sec = (int(x) for x in hms.split(":"))
        dt = datetime.datetime(
            year, month, day, hour, minute, sec, tzinfo=datetime.timezone.utc
        )
        return int(dt.timestamp())
    except Exception:
        return 0


def _host_of(url: str) -> str:
    if not url.startswith("https://"):
        return ""
    rest = url[len("https://"):]
    host = rest.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    if "@" in host or not host:
        return ""
    return host.split(":")[0].lower()


def _pick(data, path: str):
    cur = data
    for part in path.split("."):
        if isinstance(cur, list):
            cur = cur[int(part)]
        elif isinstance(cur, dict):
            cur = cur[part]
        else:
            raise KeyError(part)
    return cur


def _to_micro(value) -> int:
    """Normalise a price to integer micro-USD; reject junk values."""
    if isinstance(value, bool) or value is None:
        raise ValueError("price missing")
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError("price not finite")
    if number <= 0:
        raise ValueError("price not positive")
    return int(round(number * MICRO))


def _fmt(micro: int) -> str:
    return f"{micro // MICRO}.{micro % MICRO:06d}"


def _llm_price(body: str) -> float:
    prompt = (
        "Extract the current USD price from the API response below.\n"
        'Respond ONLY with JSON: {"price": <number>}. '
        'If there is no USD price, respond {"price": null}.\n\n'
        "API RESPONSE:\n" + body[:3000]
    )
    res = gl.nondet.exec_prompt(prompt, response_format="json")
    if not isinstance(res, dict):
        raise gl.vm.UserError(f"{ERROR_LLM} non-dict response")
    return res.get("price")


def _fetch_one(src: dict, tx_time: int, max_age: int) -> int:
    resp = gl.nondet.web.get(src["url"])
    if resp.status != 200:
        raise ValueError(f"http {resp.status}")
    if resp.body is None:
        raise ValueError("empty body")
    body = resp.body.decode("utf-8")

    data = None
    path = src.get("path", "")
    if path:
        data = json.loads(body)
        raw_price = _pick(data, path)
    else:
        raw_price = _llm_price(body)

    ts_path = src.get("ts_path", "")
    if ts_path and tx_time:
        if data is None:
            data = json.loads(body)
        source_ts = int(float(_pick(data, ts_path)))
        if tx_time - source_ts > max_age:
            raise ValueError("stale data")
        if source_ts - tx_time > 120:
            raise ValueError("timestamp in the future")

    return _to_micro(raw_price)


def _collect(sources, min_sources: int, max_age: int, max_dev_bps: int, tx_time: int) -> dict:
    """Runs inside the nondeterministic block (leader and validators)."""
    prices = []
    reasons = []
    for src in sources:
        try:
            prices.append(_fetch_one(src, tx_time, max_age))
        except Exception as exc:
            reasons.append(f"{_host_of(src['url'])}: {str(exc)[:40]}")

    if len(prices) < min_sources:
        return {"status": "invalid", "reason": "too few valid sources; " + "; ".join(reasons)}

    ordered = sorted(prices)
    median = ordered[len(ordered) // 2]
    for p in ordered:
        if abs(p - median) * 10000 > median * max_dev_bps:
            return {"status": "invalid", "reason": "sources disagree"}

    return {"status": "ok", "price": median, "sources": len(prices)}


# --------------------------------------------------------------- contract

class PriceGuard(gl.Contract):
    owner: Address
    allowed_hosts: TreeMap[str, str]
    assets: TreeMap[str, str]
    asset_symbols: DynArray[str]
    prices: TreeMap[str, str]
    history: TreeMap[str, str]
    last_error: TreeMap[str, str]

    def __init__(self):
        # Constructor runs exactly once at deployment: ownership is one-time.
        self.owner = _sender()
        for host in DEFAULT_HOSTS:
            self.allowed_hosts[host] = "1"

    def _only_owner(self):
        if _sender() != self.owner:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Only owner")

    # ------------------------------------------------------------- admin

    @gl.public.write
    def addAllowedHost(self, host: str) -> None:
        self._only_owner()
        host = host.strip().lower()
        if not host or "/" in host or ":" in host or "@" in host:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Invalid host")
        self.allowed_hosts[host] = "1"

    @gl.public.write
    def removeAllowedHost(self, host: str) -> None:
        self._only_owner()
        self.allowed_hosts[host.strip().lower()] = "0"

    @gl.public.write
    def registerAsset(
        self,
        symbol: str,
        sources_json: str,
        min_sources: int,
        max_age_sec: int,
        max_dev_bps: int,
        max_jump_bps: int,
    ) -> None:
        """
        sources_json example:
        [{"url": "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd&include_last_updated_at=true",
          "path": "bitcoin.usd", "ts_path": "bitcoin.last_updated_at"}]
        An empty "path" lets the LLM extract the price instead.
        From the genlayer CLI pass it as: 'json:[{...}]' (see RUNBOOK).
        """
        self._only_owner()

        # The genlayer CLI turns any argument that looks like a JSON array into
        # a native array, so a CLI caller can prefix the string with "json:".
        if sources_json.startswith("json:"):
            sources_json = sources_json[5:]

        if not symbol or len(symbol) > 16 or not symbol.isalnum():
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Invalid symbol")

        try:
            sources = json.loads(sources_json)
        except Exception:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} sources_json is not valid JSON")
        if not isinstance(sources, list) or not (1 <= len(sources) <= MAX_SOURCES):
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Need 1 to {MAX_SOURCES} sources")

        clean = []
        for src in sources:
            if not isinstance(src, dict):
                raise gl.vm.UserError(f"{ERROR_EXPECTED} Bad source entry")
            url = str(src.get("url", ""))
            host = _host_of(url)
            if not host:
                raise gl.vm.UserError(f"{ERROR_EXPECTED} Source must be an https URL")
            if host not in self.allowed_hosts or self.allowed_hosts[host] != "1":
                raise gl.vm.UserError(f"{ERROR_EXPECTED} Host not allowlisted: {host}")
            clean.append(
                {
                    "url": url,
                    "path": str(src.get("path", "")),
                    "ts_path": str(src.get("ts_path", "")),
                }
            )

        if not (1 <= min_sources <= len(clean)):
            raise gl.vm.UserError(f"{ERROR_EXPECTED} min_sources out of range")
        if not (30 <= max_age_sec <= 86400):
            raise gl.vm.UserError(f"{ERROR_EXPECTED} max_age_sec must be 30..86400")
        if not (1 <= max_dev_bps <= 2000):
            raise gl.vm.UserError(f"{ERROR_EXPECTED} max_dev_bps must be 1..2000")
        if not (1 <= max_jump_bps <= 10000):
            raise gl.vm.UserError(f"{ERROR_EXPECTED} max_jump_bps must be 1..10000")

        if symbol not in self.assets:
            self.asset_symbols.append(symbol)
        self.assets[symbol] = json.dumps(
            {
                "symbol": symbol,
                "sources": clean,
                "min_sources": min_sources,
                "max_age": max_age_sec,
                "max_dev_bps": max_dev_bps,
                "max_jump_bps": max_jump_bps,
            }
        )

    @gl.public.write
    def resetPrice(self, symbol: str) -> None:
        """Owner escape hatch after a legitimate jump tripped the circuit breaker."""
        self._only_owner()
        if symbol not in self.assets:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Asset not found")
        self.prices[symbol] = json.dumps({"price": 0, "sources": 0, "updated_at": 0})

    # ------------------------------------------------------------ oracle

    @gl.public.write
    def updatePrice(self, symbol: str) -> None:
        if symbol not in self.assets:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Asset not found")

        cfg = json.loads(self.assets[symbol])
        sources = cfg["sources"]
        min_sources = int(cfg["min_sources"])
        max_age = int(cfg["max_age"])
        max_dev_bps = int(cfg["max_dev_bps"])
        max_jump_bps = int(cfg["max_jump_bps"])
        tx_time = _tx_time()

        prev_price = 0
        prev_time = 0
        if symbol in self.prices:
            prev = json.loads(self.prices[symbol])
            prev_price = int(prev["price"])
            prev_time = int(prev["updated_at"])
        if tx_time and prev_time and tx_time - prev_time < MIN_UPDATE_INTERVAL:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Updated too recently")

        def leader_fn() -> dict:
            return _collect(sources, min_sources, max_age, max_dev_bps, tx_time)

        def validator_fn(leaders_res: gl.vm.Result) -> bool:
            if not isinstance(leaders_res, gl.vm.Return):
                return False
            leader = leaders_res.calldata
            mine = leader_fn()
            if leader["status"] != mine["status"]:
                return False
            if leader["status"] != "ok":
                return True  # both rejected the data
            lp = int(leader["price"])
            mp = int(mine["price"])
            return abs(lp - mp) * 10000 <= max(lp, mp) * max_dev_bps

        result = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)

        if result["status"] != "ok":
            self._record_error(symbol, str(result.get("reason", "invalid")), tx_time)
            return

        new_price = int(result["price"])
        if prev_price > 0 and abs(new_price - prev_price) * 10000 > prev_price * max_jump_bps:
            self._record_error(symbol, "price jump exceeds circuit breaker", tx_time)
            return

        self.prices[symbol] = json.dumps(
            {
                "price": new_price,
                "sources": int(result["sources"]),
                "updated_at": tx_time,
            }
        )
        entries = json.loads(self.history[symbol]) if symbol in self.history else []
        entries.append({"price": str(new_price), "at": tx_time})
        self.history[symbol] = json.dumps(entries[-HISTORY_LIMIT:])

    def _record_error(self, symbol: str, reason: str, tx_time: int) -> None:
        self.last_error[symbol] = json.dumps({"reason": reason[:200], "at": tx_time})

    # -------------------------------------------------------------- views

    @gl.public.view
    def getPrice(self, symbol: str) -> dict:
        if symbol not in self.prices:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Price not found")
        rec = json.loads(self.prices[symbol])
        if int(rec["price"]) <= 0:
            raise gl.vm.UserError(f"{ERROR_EXPECTED} Price not available")
        return {
            "symbol": symbol,
            "price": _fmt(int(rec["price"])),
            "price_micro": str(rec["price"]),
            "currency": "USD",
            "sources": str(rec["sources"]),
            "updated_at": str(rec["updated_at"]),
        }

    @gl.public.view
    def getLastError(self, symbol: str) -> dict:
        if symbol not in self.last_error:
            return {}
        return json.loads(self.last_error[symbol])

    @gl.public.view
    def getPriceHistory(self, symbol: str) -> list:
        if symbol not in self.history:
            return []
        return json.loads(self.history[symbol])

    @gl.public.view
    def getAsset(self, symbol: str) -> dict:
        if symbol not in self.assets:
            return {}
        return json.loads(self.assets[symbol])

    @gl.public.view
    def getAllAssets(self) -> list:
        return [json.loads(self.assets[s]) for s in self.asset_symbols]

    @gl.public.view
    def getStats(self) -> dict:
        return {
            "total_assets": str(len(self.asset_symbols)),
            "owner": str(self.owner),
        }

# v1.0.0
# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
import json
import genlayer.gl as gl

class PriceGuard(gl.Contract):
    def __init__(self):
        self.owner = ""
        self.asset_count = "0"
        self.assets = "{}"
        self.prices = "{}"
        self.history = "{}"

    @gl.public.write
    def init(self):
        self.owner = str(gl.message.sender_address)

    def _load_assets(self):
        data = getattr(self, "assets", "{}")
        if isinstance(data, str):
            return json.loads(data) if data else {}
        return data if data is not None else {}

    def _save_assets(self, assets):
        self.assets = json.dumps(assets)

    def _load_prices(self):
        data = getattr(self, "prices", "{}")
        if isinstance(data, str):
            return json.loads(data) if data else {}
        return data if data is not None else {}

    def _save_prices(self, prices):
        self.prices = json.dumps(prices)

    def _load_history(self):
        data = getattr(self, "history", "{}")
        if isinstance(data, str):
            return json.loads(data) if data else {}
        return data if data is not None else {}

    def _save_history(self, history):
        self.history = json.dumps(history)

    @gl.public.write
    def registerAsset(self, symbol: str, url: str):
        if str(gl.message.sender_address) != self.owner:
            raise ValueError("Only owner can register assets")
        assets = self._load_assets()
        count = int(self.asset_count) + 1
        self.asset_count = str(count)
        assets[symbol] = {
            "id": str(count),
            "symbol": symbol,
            "url": url,
            "registered_at": str(gl.block.timestamp)
        }
        self._save_assets(assets)

    @gl.public.write
    def updatePrice(self, symbol: str):
        assets = self._load_assets()
        if symbol not in assets:
            raise ValueError("Asset not found")

        def fetch_price():
            response = gl.nondet.web.get(assets[symbol]["url"])
            return response.body.decode("utf-8")

        raw_data = gl.eq_principle.strict_eq(fetch_price)

        prompt = """You are a financial data parser. Extract the exact USD price from the following API response.

API RESPONSE:
""" + raw_data[:3000] + """

Respond ONLY with valid JSON in this exact format:
{"price": 1234.56, "currency": "USD", "timestamp": "2024-01-01T00:00:00Z"}"""

        def parse_price():
            result = gl.nondet.exec_prompt(prompt)
            if hasattr(result, "get"):
                result = result.get()
            if isinstance(result, dict):
                return result
            if isinstance(result, str):
                try:
                    return json.loads(result)
                except:
                    pass
            return {"price": 0.0, "currency": "USD", "timestamp": "0"}

        parsed = gl.eq_principle.json_eq(parse_price)

        try:
            price = float(parsed.get("price", 0))
        except:
            price = 0.0

        prices = self._load_prices()
        prices[symbol] = {
            "price": str(price),
            "currency": parsed.get("currency", "USD"),
            "timestamp": str(gl.block.timestamp),
            "block": str(gl.block.number)
        }
        self._save_prices(prices)

        history = self._load_history()
        if symbol not in history:
            history[symbol] = []
        history[symbol].append({
            "price": str(price),
            "timestamp": str(gl.block.timestamp),
            "block": str(gl.block.number)
        })
        if len(history[symbol]) > 50:
            history[symbol] = history[symbol][-50:]
        self._save_history(history)

        return str(price)

    @gl.public.view
    def getPrice(self, symbol: str):
        prices = self._load_prices()
        if symbol not in prices:
            raise ValueError("Price not found")
        return prices[symbol]

    @gl.public.view
    def getPriceHistory(self, symbol: str):
        history = self._load_history()
        if symbol not in history:
            return []
        return history[symbol]

    @gl.public.view
    def getAllAssets(self):
        return list(self._load_assets().values())

    @gl.public.view
    def getStats(self):
        assets = self._load_assets()
        prices = self._load_prices()
        return {
            "total_assets": str(len(assets)),
            "priced_assets": str(len(prices)),
            "owner": self.owner
        }

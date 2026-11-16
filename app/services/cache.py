import time


class TTLCache:
    def __init__(self, ttl: int = 3600, max_items: int = 200):
        self.ttl, self.max_items = ttl, max_items
        self._d: dict = {}

    def get(self, key):
        v = self._d.get(key)
        if v and time.time() - v[0] < self.ttl:
            return v[1]
        self._d.pop(key, None)
        return None

    def set(self, key, value):
        if len(self._d) >= self.max_items:
            self._d.pop(min(self._d, key=lambda k: self._d[k][0]), None)
        self._d[key] = (time.time(), value)

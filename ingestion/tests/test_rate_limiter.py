import time
import pytest
from unittest.mock import MagicMock
from app.services.rate_limiter import RateLimiter
from app.config import Settings


class MockRedisPipeline:
    def __init__(self, parent):
        self.parent = parent
        self.commands = []

    def zremrangebyscore(self, key, min_s, max_s):
        self.commands.append(("zremrangebyscore", key, min_s, max_s))
        return self

    def zcard(self, key):
        self.commands.append(("zcard", key))
        return self

    def zrangebyscore(self, key, min_s, max_s, withscores=False):
        self.commands.append(("zrangebyscore", key, min_s, max_s, withscores))
        return self

    def get(self, key):
        self.commands.append(("get", key))
        return self

    def zadd(self, key, mapping):
        self.commands.append(("zadd", key, mapping))
        return self

    def expire(self, key, ttl):
        self.commands.append(("expire", key, ttl))
        return self

    def incrby(self, key, amount):
        self.commands.append(("incrby", key, amount))
        return self

    def execute(self):
        results = []
        for cmd in self.commands:
            action = cmd[0]
            if action == "zremrangebyscore":
                results.append(0)
            elif action == "zcard":
                results.append(self.parent.rpm_count)
            elif action == "zrangebyscore":
                results.append(self.parent.tpm_entries)
            elif action == "get":
                results.append(str(self.parent.rpd_count))
            elif action in ("zadd", "expire", "incrby"):
                results.append(1)
        return results


class MockRedisClient:
    def __init__(self):
        self.rpm_count = 0
        self.tpm_entries = []
        self.rpd_count = 0

    def pipeline(self):
        return MockRedisPipeline(self)

    def zrange(self, key, start, stop, withscores=False):
        if key == "rate_limit:rpm_window" and self.rpm_count > 0:
            return [("req1", time.time() - 30.0)]
        return []


def test_rate_limiter_can_consume_within_limits():
    mock_redis = MockRedisClient()
    mock_redis.rpm_count = 10
    mock_redis.tpm_entries = [("12345:1000:abc", time.time())]
    mock_redis.rpd_count = 100

    limiter = RateLimiter(redis_client=mock_redis)
    allowed, wait_sec, reason = limiter.can_consume(tokens=5000, requests=1)

    assert allowed is True
    assert wait_sec == 0.0
    assert reason == "OK"


def test_rate_limiter_exceeds_tpm_limit():
    mock_redis = MockRedisClient()
    now = time.time()
    # 23,000 tokens already in window
    mock_redis.tpm_entries = [(f"{now}:23000:abc", now)]

    limiter = RateLimiter(redis_client=mock_redis)
    # Requesting 3,000 tokens pushes over 24,000 safety limit
    allowed, wait_sec, reason = limiter.can_consume(tokens=3000, requests=1)

    assert allowed is False
    assert wait_sec > 0.0
    assert "TPM limit approaching" in reason


def test_rate_limiter_exceeds_rpm_limit():
    mock_redis = MockRedisClient()
    # 80 RPM already recorded (safety limit is 80)
    mock_redis.rpm_count = 80

    limiter = RateLimiter(redis_client=mock_redis)
    allowed, wait_sec, reason = limiter.can_consume(tokens=500, requests=1)

    assert allowed is False
    assert wait_sec > 0.0
    assert "RPM limit reached" in reason


def test_rate_limiter_exceeds_rpd_limit():
    mock_redis = MockRedisClient()
    # 950 daily requests already recorded (safety limit is 950)
    mock_redis.rpd_count = 950

    limiter = RateLimiter(redis_client=mock_redis)
    allowed, wait_sec, reason = limiter.can_consume(tokens=500, requests=1)

    assert allowed is False
    assert wait_sec > 0.0
    assert "Daily request limit reached" in reason


def test_rate_limiter_get_quota_stats():
    mock_redis = MockRedisClient()
    mock_redis.rpm_count = 20
    now = time.time()
    mock_redis.tpm_entries = [f"{now}:12000:abc", f"{now}:4000:def"]
    mock_redis.rpd_count = 150

    limiter = RateLimiter(redis_client=mock_redis)
    stats = limiter.get_quota_stats()

    assert stats["rpm"]["used"] == 20
    assert stats["rpm"]["limit"] == 80
    assert stats["tpm"]["used"] == 16000
    assert stats["tpm"]["limit"] == 24000
    assert stats["rpd"]["used"] == 150
    assert stats["rpd"]["limit"] == 950

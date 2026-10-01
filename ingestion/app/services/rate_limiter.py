import time
import uuid
import logging
from datetime import datetime, timezone
from typing import Tuple, Dict, Any, Optional
import redis

from app.config import get_settings

logger = logging.getLogger(__name__)


class RateLimiter:
    """
    Distributed Redis-backed sliding-window rate limiter enforcing:
      - 100 RPM (Safety target: 80 RPM)
      - 30,000 TPM (Safety target: 24,000 TPM)
      - 1,000 RPD (Safety target: 950 RPD)
    """

    def __init__(self, redis_client: Optional[redis.Redis] = None):
        self.settings = get_settings()
        if redis_client is not None:
            self.redis = redis_client
        else:
            try:
                self.redis = redis.Redis.from_url(self.settings.redis_url, decode_responses=True)
            except Exception as e:
                logger.warning(f"Could not connect to Redis at {self.settings.redis_url}: {e}. Local fallback enabled.")
                self.redis = None

        # Keys
        self.rpm_key = "rate_limit:rpm_window"
        self.tpm_key = "rate_limit:tpm_window"

    def _get_rpd_key(self) -> str:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return f"rate_limit:rpd:{today}"

    def can_consume(self, tokens: int, requests: int = 1) -> Tuple[bool, float, str]:
        """
        Checks if the requested tokens and request count can be consumed within limits.
        Returns:
            (allowed: bool, wait_seconds: float, reason: str)
        """
        if not self.redis:
            return True, 0.0, "OK (No Redis)"

        now = time.time()
        window_start = now - 60.0

        try:
            pipe = self.redis.pipeline()

            # 1. Clean up windows older than 60 seconds
            pipe.zremrangebyscore(self.rpm_key, "-inf", window_start)
            pipe.zremrangebyscore(self.tpm_key, "-inf", window_start)

            # 2. Query current RPM and TPM window items
            pipe.zcard(self.rpm_key)
            pipe.zrangebyscore(self.tpm_key, window_start, "+inf", withscores=True)

            # 3. Query today's RPD
            rpd_key = self._get_rpd_key()
            pipe.get(rpd_key)

            results = pipe.execute()

            current_rpm = int(results[2]) if results[2] else 0
            tpm_entries = results[3] or []
            current_rpd = int(results[4]) if results[4] else 0

            # Calculate current TPM usage
            current_tpm = 0
            earliest_tpm_entry = None
            for entry, score in tpm_entries:
                # entry is formatted as "{timestamp}:{tokens}:{uuid}"
                parts = entry.split(":")
                if len(parts) >= 2:
                    current_tpm += int(parts[1])
                if earliest_tpm_entry is None:
                    earliest_tpm_entry = (entry, score)

            # Check RPD
            if current_rpd + requests > self.settings.RATE_LIMIT_MAX_RPD:
                # Need to wait until tomorrow UTC
                seconds_to_tomorrow = 86400 - (int(now) % 86400)
                return (
                    False,
                    float(seconds_to_tomorrow),
                    f"Daily request limit reached ({current_rpd}/{self.settings.RATE_LIMIT_MAX_RPD} RPD)",
                )

            # Check RPM
            if current_rpm + requests > self.settings.RATE_LIMIT_MAX_RPM:
                # Find oldest entry in RPM window to estimate wait
                oldest_rpm = self.redis.zrange(self.rpm_key, 0, 0, withscores=True)
                if oldest_rpm:
                    wait_time = max(0.5, (oldest_rpm[0][1] + 60.0) - now)
                else:
                    wait_time = 1.0
                return (
                    False,
                    round(wait_time, 2),
                    f"RPM limit reached ({current_rpm}/{self.settings.RATE_LIMIT_MAX_RPM} RPM)",
                )

            # Check TPM
            if current_tpm + tokens > self.settings.RATE_LIMIT_MAX_TPM:
                if earliest_tpm_entry:
                    wait_time = max(0.5, (earliest_tpm_entry[1] + 60.0) - now)
                else:
                    wait_time = 1.0
                return (
                    False,
                    round(wait_time, 2),
                    f"TPM limit approaching ({current_tpm + tokens}/{self.settings.RATE_LIMIT_MAX_TPM} TPM)",
                )

            return True, 0.0, "OK"

        except Exception as e:
            logger.error(f"Error checking rate limits in Redis: {e}")
            return True, 0.0, "Error bypassed"

    def record_consumption(self, tokens: int, requests: int = 1) -> None:
        """Atomically records consumed tokens and requests in Redis."""
        if not self.redis:
            return

        now = time.time()
        rpd_key = self._get_rpd_key()

        try:
            pipe = self.redis.pipeline()

            # Record RPM
            for _ in range(requests):
                req_id = f"{now}:{uuid.uuid4().hex[:8]}"
                pipe.zadd(self.rpm_key, {req_id: now})
            pipe.expire(self.rpm_key, 120)

            # Record TPM
            token_entry = f"{now}:{tokens}:{uuid.uuid4().hex[:8]}"
            pipe.zadd(self.tpm_key, {token_entry: now})
            pipe.expire(self.tpm_key, 120)

            # Record RPD
            pipe.incrby(rpd_key, requests)
            pipe.expire(rpd_key, 90000)  # 25 hours

            pipe.execute()
        except Exception as e:
            logger.error(f"Error recording consumption in Redis: {e}")

    def acquire(self, tokens: int, requests: int = 1, max_wait: float = 300.0) -> bool:
        """
        Blocks smoothly until quota is available or max_wait is exceeded.
        Returns True if acquired, False if timed out.
        """
        start_time = time.time()
        while True:
            allowed, wait_seconds, reason = self.can_consume(tokens, requests)
            if allowed:
                self.record_consumption(tokens, requests)
                return True

            if (time.time() - start_time) + wait_seconds > max_wait:
                logger.warning(f"RateLimiter: Exceeded max wait of {max_wait}s. Reason: {reason}")
                return False

            sleep_duration = min(wait_seconds + 0.1, 5.0)
            logger.info(f"Rate limit throttle: {reason}. Sleeping {sleep_duration:.1f}s...")
            time.sleep(sleep_duration)

    def get_quota_stats(self) -> Dict[str, Any]:
        """Returns real-time usage statistics for the web UI meters."""
        if not self.redis:
            return {
                "rpm": {"used": 0, "limit": self.settings.RATE_LIMIT_MAX_RPM, "api_limit": 100},
                "tpm": {"used": 0, "limit": self.settings.RATE_LIMIT_MAX_TPM, "api_limit": 30000},
                "rpd": {"used": 0, "limit": self.settings.RATE_LIMIT_MAX_RPD, "api_limit": 1000},
                "status": "connected" if self.redis else "mock",
            }

        now = time.time()
        window_start = now - 60.0

        try:
            pipe = self.redis.pipeline()
            pipe.zremrangebyscore(self.rpm_key, "-inf", window_start)
            pipe.zremrangebyscore(self.tpm_key, "-inf", window_start)
            pipe.zcard(self.rpm_key)
            pipe.zrangebyscore(self.tpm_key, window_start, "+inf")
            pipe.get(self._get_rpd_key())
            results = pipe.execute()

            used_rpm = int(results[2]) if results[2] else 0
            tpm_entries = results[3] or []
            used_rpd = int(results[4]) if results[4] else 0

            used_tpm = 0
            for entry in tpm_entries:
                parts = entry.split(":")
                if len(parts) >= 2:
                    try:
                        used_tpm += int(parts[1])
                    except ValueError:
                        pass

            return {
                "rpm": {
                    "used": used_rpm,
                    "limit": self.settings.RATE_LIMIT_MAX_RPM,
                    "api_limit": 100,
                    "percent": min(100.0, round((used_rpm / self.settings.RATE_LIMIT_MAX_RPM) * 100, 1)),
                },
                "tpm": {
                    "used": used_tpm,
                    "limit": self.settings.RATE_LIMIT_MAX_TPM,
                    "api_limit": 30000,
                    "percent": min(100.0, round((used_tpm / self.settings.RATE_LIMIT_MAX_TPM) * 100, 1)),
                },
                "rpd": {
                    "used": used_rpd,
                    "limit": self.settings.RATE_LIMIT_MAX_RPD,
                    "api_limit": 1000,
                    "percent": min(100.0, round((used_rpd / self.settings.RATE_LIMIT_MAX_RPD) * 100, 1)),
                },
                "status": "active",
            }
        except Exception as e:
            logger.error(f"Error fetching quota stats: {e}")
            return {
                "rpm": {"used": 0, "limit": self.settings.RATE_LIMIT_MAX_RPM, "api_limit": 100, "percent": 0.0},
                "tpm": {"used": 0, "limit": self.settings.RATE_LIMIT_MAX_TPM, "api_limit": 30000, "percent": 0.0},
                "rpd": {"used": 0, "limit": self.settings.RATE_LIMIT_MAX_RPD, "api_limit": 1000, "percent": 0.0},
                "status": "error",
            }

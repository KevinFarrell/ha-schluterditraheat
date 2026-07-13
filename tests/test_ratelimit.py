"""Unit tests for rate-limit parsing and budget logic (pure, no HA)."""
from __future__ import annotations

from custom_components.schluterditraheat.api import (
    RateLimit,
    parse_rate_limit_headers,
)


class TestParseHeaders:
    """Test parse_rate_limit_headers."""

    def test_parses_x_ratelimit_headers(self):
        """Test the legacy X-RateLimit-* header names."""
        rl = parse_rate_limit_headers(
            {
                "X-RateLimit-Limit": "100",
                "X-RateLimit-Remaining": "42",
                "X-RateLimit-Reset": "30",
            }
        )
        assert rl is not None
        assert rl.limit == 100
        assert rl.remaining == 42
        assert rl.reset == 30.0

    def test_case_insensitive_and_standard_names(self):
        """Test lowercase and the standard RateLimit-* names both work."""
        rl = parse_rate_limit_headers(
            {"ratelimit-remaining": "5", "ratelimit-limit": "10"}
        )
        assert rl is not None
        assert rl.remaining == 5
        assert rl.limit == 10

    def test_returns_none_when_absent(self):
        """Test that headers with no rate-limit fields yield None."""
        assert parse_rate_limit_headers({"content-type": "application/json"}) is None

    def test_tolerates_garbage_values(self):
        """Test non-numeric header values are ignored rather than raising."""
        rl = parse_rate_limit_headers(
            {"x-ratelimit-remaining": "n/a", "x-ratelimit-limit": "100"}
        )
        assert rl is not None
        assert rl.remaining is None
        assert rl.limit == 100


class TestRateLimitBudget:
    """Test the RateLimit dataclass helpers."""

    def test_is_low(self):
        """Test is_low compares remaining against the floor."""
        assert RateLimit(remaining=1).is_low(1) is True
        assert RateLimit(remaining=0).is_low(1) is True
        assert RateLimit(remaining=5).is_low(1) is False
        assert RateLimit(remaining=None).is_low(1) is False

    def test_seconds_until_reset_delta(self):
        """Test a small reset is treated as seconds-remaining from capture."""
        rl = RateLimit(reset=30.0, captured_at=1000.0)
        # 10s after capture, ~20s should remain.
        assert rl.seconds_until_reset(now=1010.0) == 20.0
        # Never negative.
        assert rl.seconds_until_reset(now=2000.0) == 0.0

    def test_seconds_until_reset_epoch(self):
        """Test a large reset is treated as an absolute epoch timestamp."""
        rl = RateLimit(reset=2_000_000_100.0, captured_at=0.0)
        assert rl.seconds_until_reset(now=2_000_000_000.0) == 100.0

    def test_seconds_until_reset_none(self):
        """Test a missing reset yields None."""
        assert RateLimit(reset=None).seconds_until_reset() is None

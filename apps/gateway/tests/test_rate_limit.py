from gateway.rate_limit import MemoryRateLimiter


async def test_allows_up_to_limit_then_blocks() -> None:
    limiter = MemoryRateLimiter(max_requests=3, window_s=60)
    assert [await limiter.allow("k") for _ in range(4)] == [True, True, True, False]


async def test_keys_are_independent() -> None:
    limiter = MemoryRateLimiter(max_requests=1, window_s=60)
    assert await limiter.allow("a")
    assert await limiter.allow("b")
    assert not await limiter.allow("a")

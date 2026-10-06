# Resource Token Bucket

A small, dependency-free token bucket for rate limiting and flow control. The bucket holds a fixed capacity of tokens, refills at a configurable rate, and `consume(n)` returns `True` if `n` tokens are available (deducting them) or `False` otherwise (deducting nothing).

## Usage

```python
from resource_token_bucket import TokenBucket

# 10 tokens/sec, max burst of 20. Default clock is time.monotonic.
bucket = TokenBucket(rate=10.0, capacity=20.0)

if bucket.consume(1):
    pass  # do work
else:
    pass  # shed load
```

For deterministic tests, inject a clock:

```python
from resource_token_bucket import TokenBucket

class FakeClock:
    def __init__(self, t=0.0):
        self.t = t
    def __call__(self):
        return self.t
    def advance(self, dt):
        self.t += dt

clk = FakeClock()
bucket = TokenBucket(rate=5.0, capacity=5.0, clock=clk)
assert bucket.consume(5) is True
clk.advance(1.0)
assert bucket.consume(5) is True
```

## Why this exists

The common need: shed load when a downstream resource is saturated, without queuing work. A token bucket gives a cheap, in-process way to decide "do this now" vs "skip this". The deliberate trade-off is that `consume` is non-blocking and all-or-nothing. There is no wait, no async path, no partial deduction, no borrowing. If you need to queue work for later, that's a different primitive and you should build it on top, not inside the bucket.

## Edges you'll hit

- **Failed consumes don't deduct.** `consume(3)` when only 2 tokens remain returns `False` and leaves the 2 in place. This matches the rate-limiting use case (a rejected request shouldn't starve the next one) but surprises people who expect a debit-on-attempt model.
- **Clock injection.** `consume` and `tokens` read the clock. If you pass a non-monotonic clock and it jumps backwards, the bucket skips refilling until the clock exceeds its previous reading, so it neither gains nor loses tokens spuriously. This is safer than naively trusting the delta, but it means a bucket paired with a broken clock can stall.
- **Zero rate is legal.** It models a fixed budget that never refills. Negative rate and non-positive capacity raise `ValueError` at construction.
- **`consume(n)` with `n <= 0` raises `ValueError`.** Asking for nothing is a caller bug, not a flow-control outcome; returning `False` there would be indistinguishable from "not enough tokens" and would mask mistakes.
- **Thread-safe.** A single internal lock serialises refill and deduction so two threads can't both see the same token count. The lock is coarse; throughput under heavy contention is traded for correctness.

## Exports

- `TokenBucket(rate, capacity, clock=None)` — constructor. `rate` is tokens/sec (>= 0), `capacity` is the max and initial token count (> 0), `clock` is a zero-arg callable returning a float seconds value (defaults to `time.monotonic`).
- `TokenBucket.consume(n=1.0) -> bool` — attempt to deduct `n` tokens.
- `TokenBucket.tokens() -> float` — current token count after refill (read-only).
- `TokenBucket.rate` / `TokenBucket.capacity` — the configured values.

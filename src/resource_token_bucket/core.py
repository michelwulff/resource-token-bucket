import time
import threading


class TokenBucket:
    """
    A token bucket that refills at a fixed rate and consumes tokens on demand,
    returning booleans for flow control under load.

    The bucket has a fixed capacity. Tokens accrue at a steady rate up to the
    capacity; once full, further accrual is discarded. consume(n) attempts to
    remove n tokens; if enough are available it succeeds and returns True,
    otherwise it fails and returns False without deducting anything.

    Design choice: a failed consume is all-or-nothing and non-blocking. There
    is no wait/await path and no partial deduction. The point of returning a
    boolean is to let the caller shed load immediately when the bucket is dry,
    not to queue work for later.

    Time is injected via the ``clock`` argument (a zero-argument callable
    returning a float, monotonic in the sense that later calls must yield
    values >= earlier values). The default is time.monotonic. If a clock ever
    goes backwards (e.g. a buggy fake, or a system clock jump on a non-
    monotonic source), the refill is skipped for that tick so the bucket
    neither gains nor loses tokens spuriously. This is deliberate: we'd rather
    a bucket stall than hand out tokens it shouldn't have.
    """

    def __init__(self, rate, capacity, clock=None):
        """
        :param rate: refill rate in tokens per second. Must be >= 0. Zero is
            permitted and means the bucket never refills after the initial
            capacity, which is useful for modelling a fixed budget.
        :param capacity: maximum tokens the bucket can hold. Must be > 0.
            Capacity is also the initial number of tokens; a fresh bucket is
            full so a caller can burst up to ``capacity`` immediately.
        :param clock: optional zero-arg callable returning a float seconds
            value. Defaults to time.monotonic. Inject a fake clock in tests so
            behaviour is deterministic and never depends on wall-clock time.
        """
        if rate < 0:
            raise ValueError("rate must be non-negative")
        if capacity <= 0:
            raise ValueError("capacity must be positive")

        self._rate = float(rate)
        self._capacity = float(capacity)
        self._clock = clock if clock is not None else time.monotonic
        self._tokens = float(capacity)
        self._last = self._clock()
        # single mutex makes consume() safe to call from multiple threads;
        # refill and deduction are done together so two threads can't both
        # see the same pre-refill token count.
        self._lock = threading.Lock()

    @property
    def rate(self):
        return self._rate

    @property
    def capacity(self):
        return self._capacity

    def tokens(self):
        """
        Current token count after applying any refill owed for elapsed time.
        This is a read-only observation; it does not deduct tokens. Mainly
        useful for instrumentation and tests.
        """
        with self._lock:
            self._refill_locked()
            return self._tokens

    def consume(self, n=1.0):
        """
        Attempt to consume n tokens.

        Returns True and deducts the tokens if n is available; returns False
        and deducts nothing if it is not. n may be fractional. n must be > 0;
        asking for zero or negative tokens is a programming error, not a flow
        control outcome, so it raises ValueError rather than returning False.
        """
        if n <= 0:
            raise ValueError("n must be positive")
        n = float(n)
        with self._lock:
            self._refill_locked()
            if self._tokens >= n:
                self._tokens -= n
                return True
            return False

    def _refill_locked(self):
        now = self._clock()
        elapsed = now - self._last
        # Guard against clock regressions. A negative or zero elapsed time
        # means we skip the refill step entirely; the bucket keeps its current
        # count and _last is only advanced when the clock actually moved
        # forward. This keeps the bucket's state monotone under a broken clock.
        if elapsed > 0:
            self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
            self._last = now

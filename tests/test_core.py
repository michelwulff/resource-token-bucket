import unittest
import time

from resource_token_bucket import TokenBucket


class FakeClock:
    """
    A controllable clock. Returns ``t`` from every call until ``advance`` is
    called. Using a fake clock means no test ever sleeps or reads the wall
    clock, so the suite is deterministic across machines and CI runners.
    """
    def __init__(self, t=0.0):
        self.t = float(t)

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += float(dt)


class TestConstruction(unittest.TestCase):
    def test_starts_full_at_capacity(self):
        clk = FakeClock(100.0)
        b = TokenBucket(rate=10.0, capacity=5.0, clock=clk)
        self.assertEqual(b.tokens(), 5.0)
        self.assertEqual(b.capacity, 5.0)
        self.assertEqual(b.rate, 10.0)

    def test_default_clock_is_monotonic(self):
        b = TokenBucket(rate=1.0, capacity=1.0)
        # Just assert it runs and returns something floaty and reasonable.
        self.assertEqual(b.tokens(), 1.0)

    def test_zero_rate_allowed(self):
        # zero rate is a legitimate degenerate case (fixed budget, no refill)
        b = TokenBucket(rate=0.0, capacity=3.0, clock=FakeClock())
        self.assertEqual(b.rate, 0.0)

    def test_negative_rate_rejected(self):
        with self.assertRaises(ValueError):
            TokenBucket(rate=-1.0, capacity=5.0, clock=FakeClock())

    def test_zero_capacity_rejected(self):
        with self.assertRaises(ValueError):
            TokenBucket(rate=1.0, capacity=0.0, clock=FakeClock())

    def test_negative_capacity_rejected(self):
        with self.assertRaises(ValueError):
            TokenBucket(rate=1.0, capacity=-5.0, clock=FakeClock())


class TestConsume(unittest.TestCase):
    def test_consume_from_full_bucket(self):
        clk = FakeClock(0.0)
        b = TokenBucket(rate=1.0, capacity=5.0, clock=clk)
        self.assertTrue(b.consume(5.0))
        self.assertEqual(b.tokens(), 0.0)

    def test_consume_more_than_available_returns_false_no_deduction(self):
        clk = FakeClock(0.0)
        b = TokenBucket(rate=1.0, capacity=5.0, clock=clk)
        self.assertTrue(b.consume(3.0))
        # 2 left, ask for 3 -> fail, and the 2 must still be there
        self.assertFalse(b.consume(3.0))
        self.assertEqual(b.tokens(), 2.0)

    def test_consume_partial_then_full_fails(self):
        clk = FakeClock(0.0)
        b = TokenBucket(rate=10.0, capacity=10.0, clock=clk)
        self.assertTrue(b.consume(0.5))
        self.assertEqual(b.tokens(), 9.5)
        self.assertFalse(b.consume(10.0))
        self.assertEqual(b.tokens(), 9.5)

    def test_consume_default_n_is_one(self):
        clk = FakeClock(0.0)
        b = TokenBucket(rate=0.0, capacity=2.0, clock=clk)
        self.assertTrue(b.consume())
        self.assertTrue(b.consume())
        self.assertFalse(b.consume())

    def test_consume_zero_or_negative_raises(self):
        b = TokenBucket(rate=1.0, capacity=1.0, clock=FakeClock())
        for bad in (0.0, -1.0, -0.001):
            with self.subTest(n=bad):
                with self.assertRaises(ValueError):
                    b.consume(bad)

    def test_fractional_consume_and_refill(self):
        clk = FakeClock(0.0)
        b = TokenBucket(rate=2.0, capacity=1.0, clock=clk)
        # start with 1.0
        self.assertTrue(b.consume(0.5))
        self.assertEqual(b.tokens(), 0.5)
        # advance 0.25s -> +0.5 tokens -> back to 1.0 (cap)
        clk.advance(0.25)
        self.assertEqual(b.tokens(), 1.0)
        self.assertTrue(b.consume(1.0))
        self.assertEqual(b.tokens(), 0.0)


class TestRefill(unittest.TestCase):
    def test_refill_proportional_to_elapsed_time(self):
        clk = FakeClock(0.0)
        b = TokenBucket(rate=10.0, capacity=10.0, clock=clk)
        # drain it
        self.assertTrue(b.consume(10.0))
        self.assertEqual(b.tokens(), 0.0)
        # 1.0s at 10/s -> 10 tokens, capped at capacity 10
        clk.advance(1.0)
        self.assertEqual(b.tokens(), 10.0)

    def test_refill_capped_at_capacity(self):
        clk = FakeClock(0.0)
        b = TokenBucket(rate=5.0, capacity=4.0, clock=clk)
        self.assertTrue(b.consume(4.0))
        # 10s at 5/s would be 50, but cap is 4
        clk.advance(10.0)
        self.assertEqual(b.tokens(), 4.0)

    def test_no_refill_when_clock_does_not_advance(self):
        clk = FakeClock(5.0)
        b = TokenBucket(rate=100.0, capacity=1.0, clock=clk)
        self.assertTrue(b.consume(1.0))
        # same time, no advance -> still empty
        self.assertEqual(b.tokens(), 0.0)

    def test_clock_regression_does_not_change_state(self):
        clk = FakeClock(10.0)
        b = TokenBucket(rate=10.0, capacity=10.0, clock=clk)
        self.assertTrue(b.consume(10.0))
        self.assertEqual(b.tokens(), 0.0)
        # clock goes backwards
        clk.t = 5.0
        self.assertEqual(b.tokens(), 0.0)
        # and once it returns past the original point, refill resumes
        clk.t = 11.0
        # 1s forward from the last good _last (10.0) -> +10 tokens, capped at 10
        self.assertEqual(b.tokens(), 10.0)

    def test_zero_rate_never_refills(self):
        clk = FakeClock(0.0)
        b = TokenBucket(rate=0.0, capacity=2.0, clock=clk)
        self.assertTrue(b.consume(2.0))
        clk.advance(1000.0)
        self.assertEqual(b.tokens(), 0.0)

    def test_consume_uses_refilled_tokens_before_deciding(self):
        clk = FakeClock(0.0)
        b = TokenBucket(rate=4.0, capacity=4.0, clock=clk)
        self.assertTrue(b.consume(4.0))
        # 0.5s at 4/s -> +2 tokens, ask for exactly 2 -> should succeed
        clk.advance(0.5)
        self.assertTrue(b.consume(2.0))
        self.assertEqual(b.tokens(), 0.0)


class TestThreading(unittest.TestCase):
    def test_concurrent_consumes_are_serialised(self):
        # With capacity 1000 and 10 threads each trying to take 100 ten times,
        # total demand is 10000. We assert the bucket hands out exactly 1000
        # (the starting capacity), because rate is 0 and we don't advance the
        # clock. This verifies the lock prevents two threads consuming the same
        # token: if it were broken, more than 1000 would succeed.
        import threading
        clk = FakeClock(0.0)
        b = TokenBucket(rate=0.0, capacity=1000.0, clock=clk)
        successes = []
        def worker():
            local = 0
            for _ in range(10):
                if b.consume(100.0):
                    local += 1
            successes.append(local)
        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sum(successes), 10)
        self.assertEqual(b.tokens(), 0.0)


if __name__ == "__main__":
    unittest.main()

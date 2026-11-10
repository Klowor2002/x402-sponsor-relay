#!/usr/bin/env python3
"""Reproducible numeric checks for the v6 Zest code in
swap-router-stx-usdcx-jing-v6.clar @ 9dab9bd (sha256 d974ea91...).
Reimplements the Clarity integer semantics exactly (floor division,
128-bit uint range) in Python and compares against ideal real-valued
bounds / true bin ticks computed with exact big-integer arithmetic.
"""
import random
from fractions import Fraction

U128 = 2**128 - 1
FEE_ONE = 10**8
WAD_PRICE = 10**26
ONE = 2**60
TWO = 2**61
LOG2_WAD = 68938602072359301496
LOG2_STEP = 16550525978195695
MAX_TICK = 4458
FRAC_BITS = [
    576460752303423488, 288230376151711744, 144115188075855872,
    72057594037927936, 36028797018963968, 18014398509481984, 9007199254740992,
    4503599627370496, 2251799813685248, 1125899906842624, 562949953421312,
    281474976710656, 140737488355328, 70368744177664, 35184372088832,
    17592186044416,
]

# ---------- zest-bound, transcribed from L1236-1245 ----------
def zest_bound(limit, fee, sell_stx):
    if sell_stx:
        return (WAD_PRICE * (FEE_ONE - fee)) // limit // FEE_ONE
    c = (WAD_PRICE * FEE_ONE + (limit - 1)) // limit
    return (c + (FEE_ONE - fee - 1)) // (FEE_ONE - fee)

def ideal_bound(limit, fee, sell_stx):
    if sell_stx:  # max-price: user needs result <= ideal
        return Fraction(WAD_PRICE * (FEE_ONE - fee), limit * FEE_ONE)
    # min-price: user needs result >= ideal
    return Fraction(WAD_PRICE * FEE_ONE, limit * (FEE_ONE - fee))

# ---------- zest-log2 / zest-tick, transcribed from L1148-1184 ----------
def zest_log2(x):
    n = x.bit_length() - 1  # Clarity (log2 x) = floor log2
    if n >= 60:
        m = x >> (n - 60)
    else:
        m = x << (60 - n)
    f = 0
    for bit in FRAC_BITS:
        sq = (m * m) // ONE
        if sq >= TWO:
            m = sq // 2
            f += bit
        else:
            m = sq
    return n * ONE + f

def zest_tick(wad):
    num = zest_log2(wad) - LOG2_WAD
    q = abs(num) // LOG2_STEP
    if num < 0:
        q = -q
        if q * LOG2_STEP != num:
            q -= 1
    t = q
    return max(-MAX_TICK, min(MAX_TICK, t))

def true_tick(wad):
    """floor(log_{1.01}(wad / 1e18)) by exact integer comparison.
    tick t means 1.01^t <= wad/1e18 < 1.01^(t+1)."""
    lo, hi = -5000, 5000
    def ge(t):  # is wad/1e18 >= 1.01^t ?
        if t >= 0:
            return wad * 100**t >= 10**18 * 101**t
        return wad * 101**(-t) * 100**0 >= 10**18 * 100**(-t) * 1 and \
            wad * 101**(-t) >= 10**18 * 100**(-t)
    # binary search largest t with ge(t)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if ge(mid):
            lo = mid
        else:
            hi = mid - 1
    return lo

def main():
    random.seed(20261111)
    # ---- check 1: zest-bound never loosens the ideal bound ----
    limits = list(range(1, 500)) + [10**k for k in range(3, 20)] + \
        [random.randint(1, 10**14) for _ in range(3000)] + \
        [31500000, 32000000, 100000000, 4294967296]
    fees = [0, 1, 100, 3000, 5000, 9999, 10**6, 5 * 10**7, FEE_ONE - 1]
    viol = loose = 0
    max_rel_excess = Fraction(0)
    for limit in limits:
        for fee in fees:
            for sell in (True, False):
                got = zest_bound(limit, fee, sell)
                ideal = ideal_bound(limit, fee, sell)
                if sell and Fraction(got) > ideal:
                    viol += 1
                if not sell and Fraction(got) < ideal:
                    viol += 1
                if ideal > 0:
                    excess = abs(Fraction(got) - ideal) / ideal
                    max_rel_excess = max(max_rel_excess, excess)
    print(f"[bound] cases={len(limits)*len(fees)*2} loosening violations={viol} "
          f"max rel deviation from ideal={float(max_rel_excess):.3e}")

    # ---- check 2: zest-tick vs true tick (exact big-int) ----
    test_wads = set()
    for t in range(-60, 61):
        if t >= 0:
            edge = (10**18 * 101**t) // 100**t
        else:
            edge = (10**18 * 100**(-t)) // 101**(-t)
        for d in (-1, 0, 1):
            if edge + d > 0:
                test_wads.add(edge + d)
    for _ in range(400):
        test_wads.add(random.randint(1, 10**27))
    test_wads.update([1, 10**18, 10**26, 10**27])
    over = under = worst = 0
    for w in sorted(test_wads):
        got = zest_tick(w)
        if abs(got) >= MAX_TICK and w > 1:
            continue  # clamp region: compare only unclamped range below
        tt = true_tick(w)
        if got > tt:
            over += 1
        if got < tt - 1:
            under += 1
        worst = max(worst, tt - got)
    print(f"[tick] cases={len(test_wads)} overestimates={over} "
          f"more-than-1-low={under} max deficit={worst}")

    # ---- check 3: zest-bound overflow reachability through zest-stage ----
    # zest-stage (L1287) evaluates dust-left BEFORE zest-bound (L1297).
    # dust-left(left, limit, sell-stx=false) passes the stage only when
    # limit-min >= 2, i.e. floor(base * 1e8 / limit) >= 2 with
    # base = left - ROUND_SLACK(2), i.e. limit <= base * 1e8 / 2.
    # limit-min's own product base * 1e8 must fit uint128 for the guard
    # itself not to abort (that abort would be in pre-existing v5-5 code).
    max_base = U128 // 10**8
    max_limit_reaching_bound = max_base * 10**8 // 2
    addition = 10**34 + (max_limit_reaching_bound - 1)
    print(f"[overflow] max base w/o limit-min overflow={max_base}")
    print(f"[overflow] max limit that can reach zest-bound="
          f"{max_limit_reaching_bound}")
    print(f"[overflow] worst-case L1243 addition={addition} "
          f"< 2^128={addition < 2**128} headroom x{2**128 // addition}")
    print(f"[overflow] claimed overflow needs limit > {2**128 - 10**34}")

    # ---- check 4: sell-stx=true branch intermediates ----
    print(f"[overflow] sell-stx=true max intermediate="
          f"{WAD_PRICE * FEE_ONE} (10^34) < 2^128={WAD_PRICE * FEE_ONE < 2**128}")

if __name__ == "__main__":
    main()

"""The statistics the findings use, each small enough to check by hand.

Wilson intervals for a share, Newcombe's interval for a difference of two
shares, Fisher's exact test, a bootstrap interval for a mean, a sign-flip
permutation test for paired differences, Firth's penalised logistic
regression (which stays finite when a cell is all one outcome), Holm's
adjustment and the power of Fisher's test.

Implements: incentives/analysis-statistics
"""

import functools
import itertools
import math

import numpy as np
from scipy import stats as sp

Z = 1.959963984540054
RNG_SEED = 20261109


def wilson(k, n, z=Z):
    """(low, high) of the Wilson score interval for k of n, or (nan, nan) when n is 0."""
    if n == 0:
        return (math.nan, math.nan)
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (max(0.0, centre - half), min(1.0, centre + half))


def newcombe(k1, n1, k2, n2):
    """Newcombe's hybrid score interval for p1 - p2 (his method 10)."""
    p1, p2 = k1 / n1, k2 / n2
    l1, u1 = wilson(k1, n1)
    l2, u2 = wilson(k2, n2)
    d = p1 - p2
    low = d - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2)
    high = d + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)
    return (low, high)


def fisher(k1, n1, k2, n2):
    """Two-sided Fisher's exact p for k1 of n1 against k2 of n2."""
    return sp.fisher_exact([[k1, n1 - k1], [k2, n2 - k2]], alternative="two-sided")[1]


def compare_counts(k1, n1, k2, n2):
    """Both shares, their difference with its interval, and Fisher's p."""
    low, high = newcombe(k1, n1, k2, n2)
    return {
        "k1": k1,
        "n1": n1,
        "k2": k2,
        "n2": n2,
        "p1": k1 / n1,
        "p2": k2 / n2,
        "diff": k1 / n1 - k2 / n2,
        "diff_low": low,
        "diff_high": high,
        "p": fisher(k1, n1, k2, n2),
    }


def bootstrap_mean(values, reps=10000, seed=RNG_SEED):
    """(mean, median, low, high): the percentile bootstrap 95% interval of the mean."""
    values = np.asarray(values, dtype=float)
    if len(values) == 0:
        return (math.nan,) * 4
    rng = np.random.default_rng(seed)
    means = rng.choice(values, size=(reps, len(values)), replace=True).mean(axis=1)
    low, high = np.percentile(means, [2.5, 97.5])
    return (float(values.mean()), float(np.median(values)), float(low), float(high))


def sign_flip(differences, seed=RNG_SEED, reps=200000):
    """Two-sided p of the mean paired difference by flipping each difference's sign.

    Exact over every sign pattern up to 16 pairs, sampled beyond that.
    """
    d = np.asarray(differences, dtype=float)
    n = len(d)
    if n == 0:
        return math.nan
    observed = abs(d.mean())
    if n <= 16:
        signs = np.array(list(itertools.product((1, -1), repeat=n)))
    else:
        signs = np.random.default_rng(seed).choice((1, -1), size=(reps, n))
    flipped = np.abs((signs * d).mean(axis=1))
    return float((flipped >= observed - 1e-12).mean())


def paired(differences):
    """Mean paired difference, its bootstrap interval and the sign-flip p."""
    mean, _, low, high = bootstrap_mean(differences)
    return {
        "n": len(differences),
        "mean": mean,
        "low": low,
        "high": high,
        "p": sign_flip(differences),
    }


def firth(x, y, zero=(), iterations=200, tolerance=1e-8):
    """Firth's penalised logistic regression: (coefficients, standard errors, penalised log-likelihood).

    x has a column of ones first. Newton steps on Firth's modified score, each
    halved until the penalised likelihood stops falling. The columns in `zero`
    are held at 0 while the penalty still uses every column, which is how the
    penalised likelihood-ratio test fits its smaller model (Heinze and Schemper).
    """
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    free = np.array([i not in set(zero) for i in range(x.shape[1])])
    beta = np.zeros(x.shape[1])

    def penalised(b):
        p = 1 / (1 + np.exp(-np.clip(x @ b, -30, 30)))
        p = np.clip(p, 1e-12, 1 - 1e-12)
        info = x.T @ (x * (p * (1 - p))[:, None])
        _, logdet = np.linalg.slogdet(info)
        return (
            float(np.sum(y * np.log(p) + (1 - y) * np.log(1 - p)) + 0.5 * logdet),
            p,
            info,
        )

    current, p, info = penalised(beta)
    for _ in range(iterations):
        inverse = np.linalg.pinv(info)
        w = p * (1 - p)
        leverage = np.einsum("ij,jk,ik->i", x * w[:, None], inverse, x)
        score = x.T @ (y - p + leverage * (0.5 - p))
        step = np.zeros_like(beta)
        step[free] = np.linalg.pinv(info[np.ix_(free, free)]) @ score[free]
        for _ in range(30):
            trial = beta + step
            value, p_new, info_new = penalised(trial)
            if value >= current - 1e-12:
                break
            step /= 2
        beta, current, p, info = trial, value, p_new, info_new
        if np.max(np.abs(step)) < tolerance:
            break
    errors = np.sqrt(np.diag(np.linalg.pinv(info)))
    return beta, errors, current


def firth_terms(x, y, names):
    """Each term's coefficient with its Wald interval, and per group of terms (named
    alike before '=') the penalised likelihood-ratio test of holding it at 0."""
    beta, errors, full = firth(x, y)
    rows = [
        {
            "term": name,
            "coef": float(beta[i]),
            "low": float(beta[i] - Z * errors[i]),
            "high": float(beta[i] + Z * errors[i]),
        }
        for i, name in enumerate(names)
    ]
    groups = {}
    for i, name in enumerate(names[1:], start=1):
        groups.setdefault(name.split("=")[0], []).append(i)
    tests = {}
    for group, columns in groups.items():
        _, _, reduced = firth(x, y, zero=columns)
        statistic = max(0.0, 2 * (full - reduced))
        tests[group] = {
            "df": len(columns),
            "chi2": statistic,
            "p": float(sp.chi2.sf(statistic, len(columns))),
        }
    return rows, tests


def holm(pvalues):
    """Holm's step-down adjusted p values, in the order given."""
    order = sorted(range(len(pvalues)), key=lambda i: pvalues[i])
    adjusted, running = [0.0] * len(pvalues), 0.0
    for rank, i in enumerate(order):
        value = min(1.0, (len(pvalues) - rank) * pvalues[i])
        running = max(running, value)
        adjusted[i] = running
    return adjusted


def fisher_power(p1, p2, table):
    """The chance Fisher's two-sided test tells p1 from p2, given which outcomes it
    rejects (`_rejects`, whose shape holds n1 and n2)."""
    n1, n2 = table.shape[0] - 1, table.shape[1] - 1
    a = sp.binom.pmf(np.arange(n1 + 1), n1, p1)
    b = sp.binom.pmf(np.arange(n2 + 1), n2, p2)
    return float(a @ table @ b)


@functools.lru_cache(maxsize=64)
def _rejects(n1, n2, alpha=0.05):
    """Which (k1, k2) outcomes Fisher's test rejects at `alpha`, as a 0/1 table."""
    table = np.zeros((n1 + 1, n2 + 1))
    for k1 in range(n1 + 1):
        for k2 in range(n2 + 1):
            table[k1, k2] = fisher(k1, n1, k2, n2) <= alpha
    return table


def detectable(n1, n2, base, power=0.8, alpha=0.05):
    """The smallest rise above `base` that Fisher's test detects with `power`, or None."""
    table = _rejects(n1, n2, alpha)
    for step in range(1, 101):
        other = base + step / 100
        if other > 1:
            return None
        if fisher_power(other, base, table) >= power:
            return step / 100
    return None


def cohen_kappa(first, second):
    """Cohen's kappa between two lists of labels, or nan when chance agreement is 1."""
    labels = sorted(set(first) | set(second))
    n = len(first)
    observed = sum(a == b for a, b in zip(first, second, strict=True)) / n
    expected = sum((first.count(lab) / n) * (second.count(lab) / n) for lab in labels)
    if expected == 1:
        return math.nan
    return (observed - expected) / (1 - expected)

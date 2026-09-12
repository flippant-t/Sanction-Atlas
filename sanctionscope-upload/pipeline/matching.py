"""Name matching, ported line for line from functions/api/v1/_lib.js.

Monitoring runs here in the nightly build, while /api/v1/screen runs the same logic in a
Cloudflare Function. If the two drift, a customer gets one answer from screening and a
different one from monitoring and stops trusting both. tests/test_matching_parity.py runs
a fixed set of pairs through both implementations and fails if any score differs.

Change nothing here without changing _lib.js the same way, and run the parity test.
"""
import re
import unicodedata

# same list as _lib.js LEGAL and api.py; the build publishes it in search/_meta.json
LEGAL = set(
    "LLC LTD LIMITED INC CORP CORPORATION CO COMPANY GMBH AG SA SAS SARL BV NV PLC PJSC "
    "JSC OJSC CJSC OAO ZAO OOO AO PAO LLP LP SRL SPA PTE PTY PVT FZE FZCO THE OF AND "
    "PUBLIC JOINT STOCK OPEN CLOSED".split()
)


def norm(s):
    """JS: (s||"").normalize("NFKD").replace(/[\\u0300-\\u036f]/g,"").toUpperCase()
             .replace(/[^A-Z0-9 ]+/g," ").replace(/\\s+/g," ").trim()"""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not ("\u0300" <= c <= "\u036f")).upper()
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9 ]+", " ", s)).strip()


def tokens(s):
    return [t for t in norm(s).split(" ") if len(t) > 1 and t not in LEGAL]


def jw(a, b):
    """Jaro-Winkler similarity, 0..1. Same arithmetic and same order of operations as the JS."""
    if a == b:
        return 1.0
    la, lb = len(a), len(b)
    if not la or not lb:
        return 0.0
    rng = max(0, max(la, lb) // 2 - 1)
    ma, mb = [False] * la, [False] * lb
    m = 0
    for i in range(la):
        lo, hi = max(0, i - rng), min(lb - 1, i + rng)
        for j in range(lo, hi + 1):
            if not mb[j] and a[i] == b[j]:
                ma[i] = mb[j] = True
                m += 1
                break
    if not m:
        return 0.0
    t = 0
    k = 0
    for i in range(la):
        if ma[i]:
            while not mb[k]:
                k += 1
            if a[i] != b[k]:
                t += 1
            k += 1
    j = (m / la + m / lb + (m - t / 2) / m) / 3
    l = 0
    while l < 4 and l < la and l < lb and a[l] == b[l]:
        l += 1
    return j + l * 0.1 * (1 - j)


def score(query, names):
    """Best score of `query` against a party's name and up to 3 aliases.

    Each query token takes its best match in the candidate name; the score is average
    coverage of the query, lightly penalised when the listed name carries extra words.
    """
    qt = tokens(query)
    if not qt:
        return 0.0
    best = 0.0
    for name in names:
        nt = tokens(name)
        if not nt:
            continue
        total = 0.0
        for a in qt:
            m = 0.0
            for b in nt:
                s = 1.0 if a == b else jw(a, b)
                if s > m:
                    m = s
            total += m if m >= 0.8 else m * 0.5
        cov = total / len(qt)
        extra = max(0, len(nt) - len(qt))
        s = cov * max(0.8, 1 - 0.04 * extra)
        if s > best:
            best = s
        if best >= 0.999:
            break
    return best


def candidate_names(party):
    """The names _lib.js scoreRec sees: rec[1] (name) plus the first 3 of rec[6] (aliases)."""
    return [party["n"]] + (party.get("alt") or [])[:3]


class Index:
    """Token -> party index, the local equivalent of the search shards the Function reads.

    Candidate generation has to match _lib.js too: it takes the three least common query
    tokens, unseen tokens first, and unions their postings.
    """

    def __init__(self, parties, max_posting=1200):
        self.parties = parties
        self.by_token = {}
        self.max_posting = max_posting
        for i, p in enumerate(parties):
            seen = set()
            for name in candidate_names(p):
                for t in tokens(name):
                    if t in seen:
                        continue
                    seen.add(t)
                    self.by_token.setdefault(t, []).append(i)
        # df, as published in search/_meta.json: only tokens with a big posting list
        self.df = {t: len(v) for t, v in self.by_token.items() if len(v) >= 60}

    def candidates(self, query, cap=200):
        qt = tokens(query)
        if not qt:
            return []
        ranked = sorted(set(qt), key=lambda t: self.df.get(t, 1))
        rare = [t for t in ranked if t not in self.df]
        pick = (rare or ranked)[:3]
        postings = sorted(
            (self.by_token.get(t, [])[: self.max_posting] for t in pick), key=len
        )
        hits = {}
        for ids in postings:
            for i in ids:
                hits[i] = hits.get(i, 0) + 1
        order = sorted(hits.items(), key=lambda kv: -kv[1])[:cap]
        return [self.parties[i] for i, _ in order]

    def match(self, query, threshold, limit=5):
        out = []
        for p in self.candidates(query):
            s = score(query, candidate_names(p))
            if s >= threshold:
                out.append((p, round(s, 3)))
        out.sort(key=lambda x: -x[1])
        return out[:limit]

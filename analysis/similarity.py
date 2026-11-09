import numpy as np

INF = float("inf")


# only digraphs present in BOTH windows are compared -
# l.t 5 shared gives 'not comparable'
def _shared(a, b, min_shared):
    common = a.index.intersection(b.index)
    if len(common) < min_shared:
        return None
    return a[common].to_numpy(float), b[common].to_numpy(float)


# absolute ms distance between digraphs
def shared_mae(f1, f2, part="digraph", min_shared=5):
    s = _shared(f1[part], f2[part], min_shared)
    return INF if s is None else float(np.mean(np.abs(s[0] - s[1])))


# diff divided by mean of the 2, ignores overall speed
def shared_relative(f1, f2, part="digraph", min_shared=5):
    s = _shared(f1[part], f2[part], min_shared)
    if s is None:
        return INF
    a, b = s
    return float(np.mean(np.abs(a - b) / ((a + b) / 2)))

# compares the order of digraph speeds - speed invariant
def rank_distance(f1, f2, part="digraph", min_shared=5):
    s = _shared(f1[part], f2[part], min_shared)
    if s is None:
        return INF
    ra = np.argsort(np.argsort(s[0]))
    rb = np.argsort(np.argsort(s[1]))
    n = len(ra)
    max_disorder = n * n // 2
    return float(np.abs(ra - rb).sum() / max_disorder)


# combined digraph and hold
def combined(f1, f2, w_digraph=0.7, min_shared=5):
    d = shared_relative(f1, f2, "digraph", min_shared)
    h = shared_relative(f1, f2, "hold", min_shared)
    if np.isinf(d) and np.isinf(h):
        return INF
    if np.isinf(d):
        return h
    if np.isinf(h):
        return d
    return w_digraph * d + (1 - w_digraph) * h


DISTANCES = {
    "shared_mae": shared_mae,
    "shared_relative": shared_relative,
    "rank": rank_distance,
    "combined": combined,
}


#  pairing in comparison to the other two participants
def distance_matrix(feats, distance=shared_relative):
    n = len(feats)
    m = np.zeros((n, n))
    for i in range(n):
        for j in range(i + 1, n):
            m[i, j] = m[j, i] = distance(feats[i], feats[j])
    return m


# distances between windows of the same person vs. different people
# sanity check on the similarity
def same_vs_different(feats, distance=shared_relative):
    m = distance_matrix(feats, distance)
    who = np.array([f["participant"] for f in feats])
    iu = np.triu_indices(len(feats), k=1)
    same = who[iu[0]] == who[iu[1]]
    d = m[iu]
    return d[same], d[~same]
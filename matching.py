from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import numpy as np

def build_corpus(rows):
    texts = []
    ids = []
    for r in rows:
        t = " ".join([
            (r["title"] or ""),
            (r["description"] or ""),
            (r["tags"] or ""),
            (r["category"] or ""),
            (r["location"] or ""),
        ])
        texts.append(t.strip())
        ids.append(r["id"])
    return ids, texts

def top_matches(target_row, candidate_rows, top_k=5):
    if not candidate_rows:
        return []
    all_rows = [target_row] + list(candidate_rows)
    ids, texts = build_corpus(all_rows)
    vectorizer = TfidfVectorizer(stop_words=None, max_features=5000, ngram_range=(1,2))
    X = vectorizer.fit_transform(texts)
    sims = cosine_similarity(X[0:1], X[1:]).flatten()
    order = np.argsort(-sims)[:top_k]
    out = []
    for idx in order:
        out.append((candidate_rows[idx], float(sims[idx])))
    return out

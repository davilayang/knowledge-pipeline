from evals.search.dataset import GoldNeed
from evals.search.score import Hit, Snapshot, score_view

A1 = "kos://piece/corpus/medium::https:%2F%2Fa.example%2Fpost-1"
A1_COPY = "kos://piece/corpus/medium::https:%2F%2Fb.example%2Fpost-1-copy"


def test_two_copies_of_one_piece_credit_its_need_once(snapshot_dir):
    gold = [GoldNeed("p01-g01", 1, 2, False, False, "post", (A1, A1_COPY))]
    hits = {
        1: [
            Hit("corpus", "medium::https://a.example/post-1"),
            Hit("corpus", "medium::https://b.example/post-1-copy"),
        ]
    }
    result = score_view(hits, gold, Snapshot(snapshot_dir), negatives=set())
    assert (result.found, result.total) == (1, 1)
    assert result.relevant_items == 2

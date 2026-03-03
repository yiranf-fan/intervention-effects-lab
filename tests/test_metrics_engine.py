from experimentplatform.analytics.metrics import compute_ab_metric, required_sample_size


def test_compute_ab_metric_smoke():
    res = compute_ab_metric("exp_email", "conversion_rate")
    assert "control" in res
    assert "diff_pct" in res
    assert res["diff_pct"] > 0

def test_required_sample_size_monotonic():
    n1 = required_sample_size(0.1, 0.02)
    n2 = required_sample_size(0.1, 0.05)
    assert n2 < n1  # larger effect -> smaller sample

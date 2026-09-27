from azeroth_capital.demo import create_demo


def test_demo_exercises_end_to_end_pipeline(tmp_path):
    storage, report = create_demo(tmp_path / "demo")

    assert storage.status()["runs"] == 3
    assert storage.status()["raw_snapshots"] == 3
    assert len(storage.latest_market_pairs()) == 2
    assert report.exists()

    html = report.read_text(encoding="utf-8")
    assert "Demo Volatile Reagent" in html
    assert "Market pressure watch" in html

from engine.database import Database
from engine.data.demo import make_demo_history


def test_price_cache_roundtrip(tmp_path):
    db = Database(tmp_path / "test.db")
    original = make_demo_history("7203", 20)
    db.save_prices("7203", original, "TEST")
    loaded = db.load_prices("7203", "TEST")
    assert len(loaded) == len(original)
    assert loaded.close.iloc[-1] == original.close.iloc[-1]


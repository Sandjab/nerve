import nerve.scheduler as sched_mod
from nerve.scheduler import write_segments, load_segments

def test_segments_roundtrip(tmp_path):
    segs = [("texte un", ""), ("texte deux", "b.txt")]
    write_segments(str(tmp_path), 7, segs)
    assert load_segments(str(tmp_path), 7) == segs

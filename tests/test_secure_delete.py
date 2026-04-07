from pathlib import Path

from utils.secure_delete import secure_delete, secure_delete_dir


def test_file_deleted(tmp_path: Path):
    f = tmp_path / "test.txt"
    f.write_text("sensitive data")
    secure_delete(f)
    assert not f.exists()


def test_nonexistent_file_no_crash(tmp_path: Path):
    f = tmp_path / "nope.txt"
    secure_delete(f)  # should not raise


def test_file_overwritten_before_delete(tmp_path: Path):
    f = tmp_path / "test.txt"
    f.write_bytes(b"sensitive data here")
    secure_delete(f)
    assert not f.exists()


def test_secure_delete_dir(tmp_path: Path):
    d = tmp_path / "mydir"
    d.mkdir()
    (d / "a.txt").write_text("aaa")
    (d / "b.txt").write_text("bbb")
    secure_delete_dir(d)
    assert not d.exists()


def test_secure_delete_dir_nonexistent(tmp_path: Path):
    d = tmp_path / "nope"
    secure_delete_dir(d)  # should not raise


def test_secure_delete_empty_file(tmp_path: Path):
    f = tmp_path / "empty.txt"
    f.write_text("")
    secure_delete(f)
    assert not f.exists()

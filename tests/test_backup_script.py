"""
scripts/backup.sh với `docker` giả: tạo bản backup đủ file + sha256, giữ KEEP bản, lỗi thì
không để lại bản dở, chép sang BACKUP_MIRROR_DIR, khoá chống chạy chồng.
"""
from __future__ import annotations

import fcntl
import gzip
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "backup.sh"

pytestmark = pytest.mark.skipif(
    any(shutil.which(t) is None for t in ("bash", "flock", "gzip", "sha256sum", "zcat")),
    reason="cần bash, flock, gzip, sha256sum, zcat",
)

FAKE_DOCKER = r'''#!/usr/bin/env python3
import gzip, os, sys
a = sys.argv[1:]
log = os.environ["STUB_LOG"]
open(log, "a").write(" ".join(a) + "\n")
if a[:2] == ["ps", "-q"]:
    svc = [x.rsplit("=", 1)[1] for x in a if x.startswith("label=com.docker.compose.service=")][0]
    if not (svc == "mysql" and os.environ.get("STUB_NO_MYSQL")):
        print(f"cid-{svc}")
elif a[:1] == ["exec"]:
    cmd = a[-1]
    if "mysqldump" in cmd:
        sys.stdout.write("-- MySQL dump\nINSERT INTO pddieu VALUES (1);\n")
        if not os.environ.get("STUB_TRUNCATED"):
            sys.stdout.write("-- Dump completed on 2026-11-16\n")
    elif "COUNT(*)" in cmd:
        print("66706")
elif a[:2] == ["image", "inspect"]:
    pass
elif a[:1] == ["run"]:
    if os.environ.get("STUB_MONGO_FAIL"):
        sys.exit(1)
    vol = a[a.index("-v") + 1].split(":")[0]
    with gzip.open(os.path.join(vol, "mongo.archive.gz"), "wb") as f:
        f.write(b"mongo-archive")
    assert "MONGO_URI" in os.environ and os.environ["MONGO_URI"].startswith("mongodb")
    assert not any("mongodb" in x for x in a), "URI không được nằm trên dòng lệnh"
'''


@pytest.fixture
def env(tmp_path):
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "docker").write_text(FAKE_DOCKER)
    (stub / "docker").chmod(0o755)
    envfile = tmp_path / "env"
    envfile.write_text('X=1\nMONGODB_URI="mongodb+srv://u:p@h/x"\nMONGODB_DB=chatbot_rag\n')
    e = {**os.environ, "PATH": f"{stub}:{os.environ['PATH']}", "STUB_LOG": str(tmp_path / "docker.log"),
         "BACKUP_DIR": str(tmp_path / "backups"), "ENV_FILE": str(envfile), "KEEP": "3"}
    for k in ("BACKUP_MIRROR_DIR", "SKIP_MONGO"):
        e.pop(k, None)

    class Ctx:
        tmp = tmp_path
        backups = tmp_path / "backups"

        def run(self, **extra):
            return subprocess.run(["bash", str(SCRIPT)], env={**e, **extra}, capture_output=True, text=True,
                                  timeout=60)

        def snapshots(self, root=None):
            root = root or self.backups
            return sorted(p.name for p in root.iterdir() if p.is_dir()) if root.exists() else []

    return Ctx()


def test_success_creates_complete_backup(env):
    r = env.run()
    assert r.returncode == 0, r.stdout + r.stderr
    [snap] = env.snapshots()
    d = env.backups / snap
    assert sorted(p.name for p in d.iterdir()) == ["manifest.txt", "mongo.archive.gz", "mysql-law.sql.gz", "sha256sums"]
    assert subprocess.run(["sha256sum", "-c", "sha256sums"], cwd=d, capture_output=True).returncode == 0
    assert gzip.open(d / "mysql-law.sql.gz").read().decode().rstrip().endswith("-- Dump completed on 2026-11-16")
    manifest = (d / "manifest.txt").read_text()
    assert "pddieu=66706" in manifest and "db=chatbot_rag" in manifest
    assert oct(d.stat().st_mode)[-3:] == "700" and oct((d / "mysql-law.sql.gz").stat().st_mode)[-3:] == "600"


def test_mysqldump_flags(env):
    env.run()
    log = (env.tmp / "docker.log").read_text()
    dump_call = log[log.index("mysqldump"):].split("\n")[0]
    for flag in ("--single-transaction", "--set-gtid-purged=OFF", "--databases law", "--routines"):
        assert flag in dump_call


def test_keeps_only_last_n(env):
    names = []
    for i in range(5):
        old = env.backups / f"2026-01-0{i + 1}_000000"
        old.mkdir(parents=True)
        names.append(old.name)
    assert env.run().returncode == 0
    snaps = env.snapshots()
    assert len(snaps) == 3
    assert names[0] not in snaps and names[1] not in snaps and names[2] not in snaps
    assert names[4] in snaps


@pytest.mark.parametrize("fail", [{"STUB_NO_MYSQL": "1"}, {"STUB_MONGO_FAIL": "1"}, {"STUB_TRUNCATED": "1"}])
def test_failure_leaves_no_partial_backup(env, fail):
    r = env.run(**fail)
    assert r.returncode == 1
    assert env.snapshots() == []
    assert "THẤT BẠI" in r.stdout


def test_missing_mongo_uri_fails(env):
    (env.tmp / "env").write_text("X=1\n")
    assert env.run().returncode == 1 and env.snapshots() == []


def test_skip_mongo(env):
    r = env.run(SKIP_MONGO="1")
    assert r.returncode == 0
    [snap] = env.snapshots()
    assert not (env.backups / snap / "mongo.archive.gz").exists()


def test_mirror_copy_and_prune(env):
    mirror = env.tmp / "mirror"
    (mirror / "2025-12-31_000000").mkdir(parents=True)
    for i in range(3):
        (mirror / f"2026-01-0{i + 1}_000000").mkdir()
    r = env.run(BACKUP_MIRROR_DIR=str(mirror))
    assert r.returncode == 0, r.stdout + r.stderr
    [snap] = env.snapshots()
    assert snap in env.snapshots(mirror) and len(env.snapshots(mirror)) == 3
    assert "2025-12-31_000000" not in env.snapshots(mirror)


def test_lock_skips(env):
    env.backups.mkdir()
    with open(env.backups / ".lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        r = env.run()
    assert r.returncode == 0 and env.snapshots() == []
    assert "Đang có backup khác" in r.stdout

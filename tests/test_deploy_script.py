"""
scripts/deploy.sh + scripts/rollback.sh với `docker` giả và GitHub API giả.

Không cần Docker/GitHub thật: một script docker giả ghi lại lệnh và trả trạng thái health
theo tag; một HTTP server cục bộ đóng vai GitHub API (CI success theo sha) và URL công khai.
Repo "origin" là repo git tạm.
"""
from __future__ import annotations

import fcntl
import json
import os
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "scripts" / "deploy.sh"
ROLLBACK = ROOT / "scripts" / "rollback.sh"

pytestmark = pytest.mark.skipif(
    any(shutil.which(t) is None for t in ("bash", "git", "jq", "flock", "curl")),
    reason="cần bash, git, jq, flock, curl",
)

FAKE_DOCKER = r'''#!/usr/bin/env python3
import json, os, sys
st_path = os.environ["STUB_STATE"]
st = json.load(open(st_path)) if os.path.exists(st_path) else {}
st.setdefault("built", []); st.setdefault("health", {}); st.setdefault("calls", [])
a = sys.argv[1:]
tag = os.environ.get("IMAGE_TAG", "")
st["calls"].append({"argv": a, "tag": tag})
out, rc = "", 0
if a[:1] == ["compose"]:
    sub = next((x for x in a[1:] if x in ("build", "up", "ps", "down")), "")
    if sub == "build":
        rc = int(os.environ.get("STUB_BUILD_RC", "0"))
        if rc == 0 and tag not in st["built"]:
            st["built"].append(tag)
    elif sub == "ps":
        out = f"cid-{a[-1]}-{tag}\n"
elif a[:1] == ["inspect"]:
    cid = a[-1]
    t = cid.split("-", 2)[2]
    out = st["health"].get(t, "healthy") + "\n"
elif a[:2] == ["image", "inspect"]:
    tags = {x.split(":", 1)[1] for x in a[2:] if ":" in x}
    rc = 0 if tags and all(t in st["built"] for t in tags) else 1
elif a[:2] == ["image", "ls"]:
    out = "".join(f"{t}\n" for t in st["built"])
elif a[:2] == ["image", "rm"]:
    t = a[2].split(":", 1)[1]
    st.setdefault("removed", []).append(a[2])
    if a[2].startswith("chatbot-frontend:") and t in st["built"]:
        st["built"].remove(t)
json.dump(st, open(st_path, "w"))
sys.stdout.write(out)
sys.exit(rc)
'''


class _Api(BaseHTTPRequestHandler):
    ci_ok: set = set()
    public_code = 200

    def do_GET(self):  # noqa: N802
        u = urlparse(self.path)
        if u.path.endswith("/actions/workflows/ci.yml/runs"):
            sha = parse_qs(u.query).get("head_sha", [""])[0]
            body = json.dumps({"total_count": 1 if sha in self.ci_ok else 0}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
        elif u.path == "/public":
            self.send_response(self.public_code)
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *a):
        pass


@pytest.fixture
def env(tmp_path):
    # origin repo
    origin = tmp_path / "origin"
    origin.mkdir()
    g = lambda *a: subprocess.run(["git", "-C", str(origin), *a], check=True, capture_output=True, text=True)
    g("init", "-q", "-b", "main")
    g("config", "user.email", "t@example.invalid")
    g("config", "user.name", "t")

    def commit(msg):
        (origin / "f.txt").write_text(msg)
        g("add", "f.txt")
        g("commit", "-q", "-m", msg)
        return g("rev-parse", "HEAD").stdout.strip()

    # docker giả
    stubbin = tmp_path / "bin"
    stubbin.mkdir()
    (stubbin / "docker").write_text(FAKE_DOCKER)
    (stubbin / "docker").chmod(0o755)

    # API giả
    class Api(_Api):
        ci_ok = set()
        public_code = 200
    srv = HTTPServer(("127.0.0.1", 0), Api)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"

    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "env").write_text("X=1\n")
    (secrets / "fb.json").write_text("{}")

    e = dict(os.environ)
    e.update(
        PATH=f"{stubbin}:{e['PATH']}",
        STUB_STATE=str(tmp_path / "docker.json"),
        DEPLOY_DIR=str(tmp_path / "deploy"),
        DEPLOY_REPO_URL=str(origin),
        GITHUB_REPO="o/r",
        GITHUB_API=base,
        ENV_FILE=str(secrets / "env"),
        FIREBASE_CRED_FILE=str(secrets / "fb.json"),
        PUBLIC_URL="",
        HEALTH_TIMEOUT="2",
        PUBLIC_TIMEOUT="1",
        HEALTH_INTERVAL="0.1",
        KEEP_IMAGES="3",
    )
    e.pop("SKIP_CI_CHECK", None)
    e.pop("FORCE", None)

    class Ctx:
        tmp = tmp_path
        api = Api
        url = base

        def commit(self, msg, ci=True):
            sha = commit(msg)
            if ci:
                Api.ci_ok.add(sha)
            return sha

        def run(self, script=DEPLOY, *args, **extra):
            return subprocess.run(["bash", str(script), *args], env={**e, **extra},
                                  capture_output=True, text=True, timeout=60)

        def docker(self):
            p = tmp_path / "docker.json"
            return json.loads(p.read_text()) if p.exists() else {"calls": [], "built": [], "health": {}}

        def set_health(self, tag, status):
            st = self.docker()
            st.setdefault("health", {})[tag] = status
            (tmp_path / "docker.json").write_text(json.dumps(st))

        def state(self, name):
            p = tmp_path / "deploy" / "state" / name
            return p.read_text().strip() if p.exists() else None

        def log(self):
            p = tmp_path / "deploy" / "logs" / "deploy.log"
            return p.read_text() if p.exists() else ""

        def calls(self, sub):
            return [c for c in self.docker()["calls"] if c["argv"][:1] == ["compose"] and sub in c["argv"]]

    yield Ctx()
    srv.shutdown()


def test_first_deploy_builds_and_records(env):
    sha = env.commit("A")
    r = env.run()
    assert r.returncode == 0, r.stdout + r.stderr
    tag = sha[:12]
    assert env.state("current_tag") == tag
    assert [c["tag"] for c in env.calls("build")] == [tag]
    up = env.calls("up")
    assert up and up[-1]["tag"] == tag and "--no-build" in up[-1]["argv"]
    assert "--env-file" in up[-1]["argv"] and "-p" in up[-1]["argv"]
    assert f"Deploy {tag} OK" in env.log()


def test_noop_when_unchanged(env):
    env.commit("A")
    env.run()
    n_calls, n_log = len(env.docker()["calls"]), len(env.log())
    r = env.run()
    assert r.returncode == 0 and r.stdout == ""
    assert len(env.docker()["calls"]) == n_calls
    assert len(env.log()) == n_log


def test_waits_for_ci_and_logs_once(env):
    env.commit("A", ci=False)
    env.run()
    env.run()
    assert env.calls("build") == []
    assert env.state("current_tag") is None
    assert env.log().count("CI chưa success") == 1


def test_unhealthy_rolls_back_and_skips_failed_sha(env):
    a = env.commit("A")[:12]
    env.run()
    b = env.commit("B")[:12]
    env.set_health(b, "unhealthy")
    r = env.run()
    assert r.returncode == 1
    assert env.state("current_tag") == a
    assert env.state("failed_tag") == b
    assert env.calls("up")[-1]["tag"] == a  # rollback
    assert f"đã rollback về {a} (OK)" in env.log()
    n_build = len(env.calls("build"))
    env.run()  # sha lỗi không bị build lại mỗi 5 phút
    assert len(env.calls("build")) == n_build
    env.commit("C")  # commit mới vẫn được deploy
    env.run()
    assert len(env.calls("build")) == n_build + 1


def test_health_timeout_counts_as_failure(env):
    env.commit("A")
    env.run()
    b = env.commit("B")[:12]
    env.set_health(b, "starting")
    r = env.run(HEALTH_TIMEOUT="1")
    assert r.returncode == 1 and env.state("failed_tag") == b


def test_build_failure_keeps_current(env):
    a = env.commit("A")[:12]
    env.run()
    b = env.commit("B")[:12]
    r = env.run(STUB_BUILD_RC="1")
    assert r.returncode == 1
    assert env.state("current_tag") == a and env.state("failed_tag") == b
    assert all(c["tag"] != b for c in env.calls("up"))


def test_public_url_gate(env):
    a = env.commit("A")[:12]
    env.run(PUBLIC_URL=f"{env.url}/public")
    assert env.state("current_tag") == a
    b = env.commit("B")[:12]
    env.api.public_code = 503
    r = env.run(PUBLIC_URL=f"{env.url}/public")
    assert r.returncode == 1 and env.state("failed_tag") == b
    assert "→ 503" in env.log()


def test_first_deploy_failure_without_previous(env):
    a = env.commit("A")[:12]
    env.set_health(a, "unhealthy")
    r = env.run()
    assert r.returncode == 1
    assert env.state("current_tag") is None and env.state("failed_tag") == a
    assert "không có bản trước" in env.log()


def test_lock_held_skips(env):
    env.commit("A")
    lockdir = env.tmp / "deploy" / "state"
    lockdir.mkdir(parents=True)
    with open(lockdir / "deploy.lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        r = env.run()
    assert r.returncode == 0
    assert env.calls("build") == []
    assert "Đang có deploy/rollback khác" in env.log()


def test_prune_keeps_last_n_images(env):
    tags = []
    for m in "ABCD":
        tags.append(env.commit(m)[:12])
        assert env.run(KEEP_IMAGES="2").returncode == 0
    removed = env.docker().get("removed", [])
    assert f"chatbot-backend:{tags[0]}" in removed and f"chatbot-frontend:{tags[0]}" in removed
    assert not any(t in r for r in removed for t in tags[-2:])


def test_missing_secrets_fails_fast(env):
    env.commit("A")
    r = env.run(ENV_FILE=str(env.tmp / "khong-co"))
    assert r.returncode == 1 and "ENV_FILE" in env.log()
    assert env.calls("build") == []


def test_force_redeploys_same_sha(env):
    env.commit("A")
    env.run()
    env.run(FORCE="1")
    assert len(env.calls("build")) == 2


def test_rollback_script(env):
    a = env.commit("A")[:12]
    env.run()
    b = env.commit("B")[:12]
    env.run()
    assert env.state("current_tag") == b
    r = env.run(ROLLBACK)
    assert r.returncode == 0, r.stdout + r.stderr
    assert env.state("current_tag") == a and env.state("failed_tag") == b
    assert env.calls("up")[-1]["tag"] == a
    n = len(env.calls("build"))
    env.run()  # timer không tự deploy lại b
    assert len(env.calls("build")) == n and env.state("current_tag") == a


def test_rollback_refuses_missing_image(env):
    env.commit("A")
    env.run()
    r = env.run(ROLLBACK, "deadbeef0000")
    assert r.returncode == 1 and "không còn image" in env.log()


@pytest.mark.parametrize("url, expected", [
    ("https://github.com/owner/repo.git", "owner/repo"),
    ("https://github.com/owner/repo", "owner/repo"),
    ("git@github.com:owner/repo.git", "owner/repo"),
    ("/tmp/local/repo", ""),
])
def test_github_repo_parse(url, expected, tmp_path):
    lib = ROOT / "scripts" / "lib" / "deploy_common.sh"
    r = subprocess.run(
        ["bash", "-c", f'. "{lib}"; github_repo'],
        env={**os.environ, "DEPLOY_DIR": str(tmp_path), "DEPLOY_REPO_URL": url, "GITHUB_REPO": ""},
        capture_output=True, text=True,
    )
    assert r.stdout.strip() == expected

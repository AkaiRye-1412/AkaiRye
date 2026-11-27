"""M6 代码仓库：发现 / 只读状态 / 置顶隐藏（FR-GIT-01~05）。"""

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from app.services import git_service


def _git(*args, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, check=True)


def _init_repo(path: Path, branch: str = "main") -> None:
    path.mkdir(parents=True)
    _git("init", "-b", branch, cwd=path)
    _git("config", "user.name", "t", cwd=path)
    _git("config", "user.email", "t@t.t", cwd=path)
    (path / "README.md").write_text("# hi\n", encoding="utf-8")
    _git("add", ".", cwd=path)
    _git("commit", "-m", "init", cwd=path)


@pytest.fixture()
def code_root():
    tmp = Path(tempfile.mkdtemp(prefix="wb_test_git_"))
    a = tmp / "repo-a"
    b = tmp / "repo-b"
    _init_repo(a)
    _init_repo(b)
    # repo-b 有一个未提交变更（dirty=1）
    (b / "README.md").write_text("# hi\n\nmore\n", encoding="utf-8")
    # 噪音目录里嵌一个仓库：应被忽略规则跳过
    noise = tmp / "node_modules" / "pkg"
    _init_repo(noise)
    yield tmp
    shutil.rmtree(tmp, ignore_errors=True)


def test_discover_skips_noise(code_root):
    from app.db import database, migrations
    from app.services import config_service

    migrations.init_db()
    with database.get_conn() as conn:
        config_service.save_many(conn, {"paths": {"code_roots": [str(code_root)]}})
        repos = git_service.discover_repos(conn)

    names = sorted(Path(r).name for r in repos)
    assert names == ["repo-a", "repo-b"]  # node_modules 里的被忽略


def test_read_status_dirty_and_branch(code_root):
    b = code_root / "repo-b"
    st = git_service.read_status(str(b))
    assert st["branch"] == "main"
    assert st["dirty"] == 1
    assert st["last_commit"]


def test_refresh_and_display(code_root):
    from app.db import database, migrations
    from app.services import config_service

    migrations.init_db()
    with database.get_conn() as conn:
        config_service.save_many(conn, {"paths": {"code_roots": [str(code_root)]}})
        result = git_service.refresh(conn)
        assert result["total"] == 2

        repos = git_service.list_repos(conn)
        assert len(repos) == 2

        # 置顶 repo-a，隐藏 repo-b
        a = next(r for r in repos if r["name"] == "repo-a")
        b = next(r for r in repos if r["name"] == "repo-b")
        git_service.set_display(conn, a["path"], "pin")
        git_service.set_display(conn, b["path"], "hidden")

        visible = git_service.list_repos(conn)
        assert [r["name"] for r in visible] == ["repo-a"]
        assert visible[0]["display"] == "pin"

        with_hidden = git_service.list_repos(conn, include_hidden=True)
        assert {r["name"] for r in with_hidden} == {"repo-a", "repo-b"}

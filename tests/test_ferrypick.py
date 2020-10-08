import sys
import pytest
from pathlib import Path
from unittest import mock
import subprocess
import re

import ferrypick


U = "https://src.fedoraproject.org"


def test_parse_link_pr():
    n, p = ferrypick.parse_link(f"{U}/rpms/python-rpm-macros/pull-request/62")
    assert p == f"{U}/rpms/python-rpm-macros/pull-request/62.patch"
    assert n == "python-rpm-macros"


def test_parse_link_commit():
    hash = "f54cef86717adf4f5374820c3d5314f75b340b8b"
    n, p = ferrypick.parse_link(f"{U}/rpms/python3.7/c/{hash}?branch=master")
    assert p == f"{U}/rpms/python3.7/c/{hash}.patch"
    assert n == "python3.7"


def test_parse_link_commit_from_pr():
    hash = "6697c4ae608728bce1025ef45af"
    n, p = ferrypick.parse_link(f"{U}/fork/ca/rpms/python3.7/c/{hash}?branch=rename")
    assert p == f"{U}/fork/ca/rpms/python3.7/c/{hash}.patch"
    assert n == "python3.7"


def test_parse_link_bad():
    with pytest.raises(ValueError):
        ferrypick.parse_link(f"{U}/fork/ca/rpms/python3.7/commits/rename")


def test_rename_git_diff_spec():
    line = b"diff --git a/python3.7.spec b/python3.7.spec"
    new = ferrypick.rename(line, "python3.7", "python37")
    assert new == b"diff --git a/python37.spec b/python37.spec"


def test_rename_git_diff_rpmlintrc():
    line = b"+++ b/python3.rpmlintrc"
    new = ferrypick.rename(line, "python3", "python3.9")
    assert new == b"+++ b/python3.9.rpmlintrc"


def test_rename_git_diff_random_occurrence():
    line = b" #  remember to update the python3-docs package as well"
    new = ferrypick.rename(line, "python3-docs", "python-docs")
    assert new == line


def test_functional():
    # only mock download() and apply_patch()

    link = f"{U}/rpms/python3.9/c/a0928446.patch"
    current_name = "python3.8"
    download_content = (Path(__file__).parent / "a0928446.patch").read_bytes()
    expected = (Path(__file__).parent / "a0928446_py38.patch").read_bytes()
    argv = [sys.argv[0], link, current_name]
    apply_content = None

    def apply_patch(filename):
        nonlocal apply_content
        with open(filename, "rb") as fp:
            apply_content = fp.read()

    with mock.patch("sys.argv", argv), mock.patch.object(
        ferrypick, "download", return_value=download_content
    ), mock.patch.object(ferrypick, "apply_patch", apply_patch):
        ferrypick.main()

    assert apply_content == expected


def run(*argv, cwd, **kwargs):
    print(argv, file=sys.stderr)
    kwargs.setdefault('check', True)
    env = kwargs.setdefault('env', {})
    env.setdefault('GIT_CONFIG_NOSYSTEM', '1')
    env.setdefault('HOME', cwd)
    env.setdefault('XDG_CONFIG_HOME', cwd)
    return subprocess.run(argv, cwd=cwd, **kwargs)


def test_handle_rejects(tmp_path, monkeypatch):
    orig_path = Path(__file__).parent / "5ea55d7-python39.spec"
    expected_path = Path(__file__).parent / "expected-python39.spec"
    patch_path = Path(__file__).parent / "c8570d6.patch"

    repo_path = tmp_path / 'repo'
    file_path = repo_path / 'python39.spec'

    repo_path.mkdir()
    run('git', 'init', cwd=repo_path)
    run('git', 'config', 'user.name', 'Me', cwd=repo_path)
    run('git', 'config', 'user.email', 'me@me.test', cwd=repo_path)

    file_path.write_bytes(orig_path.read_bytes())
    run('git', 'add', 'python39.spec', cwd=repo_path)
    run('git', 'commit', '-m', 'initial', cwd=repo_path)


    argv = [sys.argv[0], patch_path, 'python39']

    monkeypatch.setattr(sys, 'argv', argv)
    monkeypatch.chdir(repo_path)
    ferrypick.main()

    date_re = re.compile(r'^\* \w{3} \w{3} \d{2} \d{4}', re.MULTILINE)
    got = date_re.sub('Xxx Xxx 00 0000', file_path.read_text())
    expected = date_re.sub('Xxx Xxx 00 0000', expected_path.read_text())

    assert got == expected


def test_handle_rejects_existing_rej(tmp_path, monkeypatch):
    # If existing .rej files are lying around, refuse to guess
    orig_path = Path(__file__).parent / "5ea55d7-python39.spec"
    expected_path = Path(__file__).parent / "expected-python39.spec"
    patch_path = Path(__file__).parent / "c8570d6.patch"

    repo_path = tmp_path / 'repo'
    file_path = repo_path / 'python39.spec'
    existing_rej_path = repo_path / 'some.rej'
    expected_rej_path = repo_path / 'python39.spec.rej'

    repo_path.mkdir()
    run('git', 'init', cwd=repo_path)
    run('git', 'config', 'user.name', 'Me', cwd=repo_path)
    run('git', 'config', 'user.email', 'me@me.test', cwd=repo_path)

    file_path.write_bytes(orig_path.read_bytes())
    run('git', 'add', 'python39.spec', cwd=repo_path)
    run('git', 'commit', '-m', 'initial', cwd=repo_path)

    existing_rej_path.write_text('...')

    argv = [sys.argv[0], patch_path, 'python39']

    monkeypatch.setattr(sys, 'argv', argv)
    monkeypatch.chdir(repo_path)
    with pytest.raises(SystemExit) as excinfo:
        ferrypick.main()
    exception = excinfo.value
    assert exception.args[0] > 0

    got = file_path.read_text()
    #print(got)
    assert 'Release: 2%{?dist}' in got

    expected = '-Release: 3%{?dist}\n+Release: 4%{?dist}\n'
    got_rej = expected_rej_path.read_text()
    print(got_rej)
    assert expected in got_rej

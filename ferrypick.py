#!/usr/bin/env python3

import os.path
import re
import subprocess
import sys
import urllib.request
import shlex
from pathlib import Path

COMMIT_RE = re.compile(r"^https://src\.fedoraproject\.org/\S+/([^/\s]+)/c/([0-9a-f]+)")
PR_RE = re.compile(r"^https://src\.fedoraproject\.org/\S+/([^/\s]+)/pull-request/\d+")
# https://docs.fedoraproject.org/en-US/packaging-guidelines/Naming/#_common_character_set_for_package_naming
PKGNAME_RE = r"[a-zA-Z0-9_.+-]+"
# Files named pkgname.spec and pkgname.rpmlintrc need to be renamed in patches
SUFFIXES_RE = r"\.(spec|rpmlintrc)"
# This is what git does: "a" in "a/python37.spec"
PREFIXES_RE = r"(a|b)"
RENAME_RE_TEMPLATE = f"(?P<prefix>{PREFIXES_RE})/{{}}(?P<suffix>{SUFFIXES_RE})"


def parse_link(link):
    """
    For a given pagure link, return package name and the patch link.
    Raise ValueError if not recognized.
    """
    for regex in COMMIT_RE, PR_RE:
        if match := regex.match(link):
            return match.group(1), match.group(0) + ".patch"
    raise ValueError("Unrecognized link")


def rename(content, original_name, current_name):
    """
    In a given bytes patch-content, replace original package name with current package name.
    If original_name is None, it replaces a more general regular expression instead.
    Works on pkgname.spec and pkgname.rpmlintrc only (as defined in SUFFIXES).
    """
    if original_name is not None and original_name == current_name:
        return content

    def replace(regs):
        prefix = regs.group("prefix")
        new_name = current_name.encode("utf8")
        suffix = regs.group("suffix")
        return b"%s/%s%s" % (prefix, new_name, suffix)

    if original_name is not None:
        name_regex = re.escape(original_name)
    else:
        name_regex = PKGNAME_RE
    regex = RENAME_RE_TEMPLATE.format(name_regex)
    regex = regex.encode("utf-8")
    content = re.sub(regex, replace, content)
    return content


def download(link):
    print(f"Downloading {link}")
    with urllib.request.urlopen(link) as response:
        content = response.read()
    return content


def stdout(cmd):
    return subprocess.check_output(cmd, shell=True, text=True).rstrip()


def execute(*cmd, **kwargs):
    print(f"$ {' '.join(shlex.quote(str(c)) for c in cmd)}")
    return subprocess.run(cmd, text=True, **kwargs)


def parse_args():
    # TODO?: Add more sophisticated argument parsing
    if len(sys.argv) < 2:
        for arg in ("COMMIT", "PR_LINK", "FILENAME"):
            print(f"Usage: {sys.argv[0]} {arg} [CURRENT_PKGNAME]")
        sys.exit(1)

    link = sys.argv[1]
    try:
        current_name = sys.argv[2]
    except IndexError:
        git_toplevel = stdout("git rev-parse --show-toplevel")
        current_name = os.path.basename(git_toplevel)

    return (link, current_name)


def get_patch_content(link):
    if os.path.exists(link):
        with open(link, "rb") as fp:
            content = fp.read()
        original_name = None
    else:
        original_name, patch_link = parse_link(link)
        content = download(patch_link)
    return (content, original_name)


def handle_reject(filename):
    """If the .rej file given in `filename` is "simple", run rmpdev-bumpspec

    Simple means roughly that only Release lines are touched and
    %changelog lines are added.

    Removes the reject file if successful.
    """
    changelog = None
    path = Path(filename)
    author = None
    with path.open() as f:
        for line in f:
            # Find first hunk header
            if line.startswith('@'):
                break
        for line in f:
            marker = line[:1]
            print(line.rstrip())
            if marker == '@':
                # Hunk header
                continue
            elif marker == ' ':
                # Context
                if line.strip() == '%changelog':
                    changelog = []
                continue
            elif marker in ('+', '-'):
                if changelog is not None:
                    if marker == '-':
                        # Removing existing changelog - bad
                        return
                    if match := re.match(
                        r'\*\s+(\S+\s+){4}(?P<author>[^>]+>)',
                        line[1:]
                    ):
                        author = match['author']
                    else:
                        changelog.append(line[1:])
                elif line[1:].startswith('Release:'):
                    continue
                else:
                    # Adding/removing something else - bad
                    return
            else:
                # Unknown line - bad
                return
    if author is None:
        print('No author found in reject')
        return
    print(f'Rejects in {filename} look harmless')
    execute(
        'rpmdev-bumpspec',
        '-u', author,
        '-c', ''.join(changelog).strip(),
        path.with_suffix(''),
        check=True,
    )
    path.unlink()


def apply_patch(filename):
    args = [
        "git", "am", "--committer-date-is-author-date", "--reject", filename,
    ]
    previous_rej = any(Path().glob(f'**/*.rej'))
    exitcode = execute(*args).returncode
    if exitcode:
        if previous_rej:
            print(
                "Not attempting to process rejected patches: "
                + "There were pre-existing *.rej files in your worktree.",
                file=sys.stderr
            )
            sys.exit(exitcode)
        print(file=sys.stderr)
        print(f"git am failed with exit code {exitcode}", file=sys.stderr)
        print(f"Patch stored as: {filename}", file=sys.stderr)

        for spec_rej in Path().glob(f'**/*.spec.rej'):
            print(f'Processing rejects in {spec_rej}')
            handle_reject(spec_rej)
        if not any(Path().glob(f'**/*.rej')):
            for spec in Path().glob(f'**/*.spec'):
                execute("git", "add", spec.relative_to(Path()), check=True)
            exitcode = execute("git", "am", "--continue").returncode

        if exitcode:
            sys.exit(exitcode)


def main():
    link, current_name = parse_args()
    content, original_name = get_patch_content(link)
    content = rename(content, original_name, current_name)

    filename = "ferrypick.patch"
    with open(filename, "wb") as fp:
        fp.write(content)
        fp.flush()

    apply_patch(filename)
    os.unlink(filename)


if __name__ == "__main__":
    main()

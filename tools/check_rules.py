#!/usr/bin/env python3
"""Mechanical checks for the hard rules in CLAUDE.md.

    python3 tools/check_rules.py

Prints every violation with the rule it breaks and how to fix it, and exits 1 if
there is any. Standard library only; reads the files git tracks.
"""
import ast
import hashlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# "No domain logic in this repo." The author's private project must not be named
# anywhere, so its vocabulary is stored as the first 16 hex digits of the SHA-256
# of each lower-cased word: the list itself names nothing.
DOMAIN_HASHES = frozenset({
    '247eba63c2e7dab4', '2e4a6ea54cc07eae', 'ad0ac7eb160de7cd', 'e763ca7d5babd568',
    'a20b56d70621ea7f', '55247229bf301d86', 'ccc5e5b6f6ab0761', '1909665238311cb2',
    '0d3d5b84bd16e8c2', 'fda8062544adc8a7', '0f7b4d8db6b78189',
})

# "English only" in code and comments: letters that English does not use but
# Catalan and Spanish do. Written as escapes so this file passes its own check.
NON_ENGLISH = re.compile('[\u00e0\u00e1\u00e8\u00e9\u00ed\u00ef\u00f2\u00f3\u00fa\u00fc'
                         '\u00e7\u00f1\u00bf\u00a1\u00c0\u00c1\u00c8\u00c9\u00cd\u00cf'
                         '\u00d2\u00d3\u00da\u00dc\u00c7\u00d1]')
CODE_SUFFIXES = ('.js', '.py')
# A line that must carry such letters on purpose (test data) says so explicitly.
ALLOW_NON_ENGLISH = 'rules: allow-non-english'
WORD = re.compile(r'[^\W\d_]+')


def domain_hash(word):
    return hashlib.sha256(word.lower().encode('utf-8')).hexdigest()[:16]


def tracked_files():
    out = subprocess.run(['git', 'ls-files', '-z'], cwd=ROOT, capture_output=True, check=True)
    return [ROOT / p for p in out.stdout.decode('utf-8').split('\0') if p]


def read_text(path):
    try:
        return path.read_bytes().decode('utf-8', errors='ignore')
    except OSError:
        return ''


def check_domain_words(files):
    problems = []
    for path in files:
        for n, line in enumerate(read_text(path).splitlines(), 1):
            if any(domain_hash(w) in DOMAIN_HASHES for w in WORD.findall(line)):
                problems.append(f'{path.relative_to(ROOT)}:{n}: a word from the private '
                                'project\'s vocabulary. "No domain logic in this repo" '
                                '(CLAUDE.md): rename it to a general term.')
    return problems


def check_english_in_code(files):
    problems = []
    for path in files:
        if path.suffix not in CODE_SUFFIXES:
            continue
        for n, line in enumerate(read_text(path).splitlines(), 1):
            if NON_ENGLISH.search(line) and ALLOW_NON_ENGLISH not in line:
                problems.append(f'{path.relative_to(ROOT)}:{n}: non-English text in code or a '
                                'comment. "English only" (CLAUDE.md): translate it, or mark deliberate '
                                f'test data with a trailing comment "{ALLOW_NON_ENGLISH}".')
    return problems


# "The Remote Script never grows a vocabulary": its generic operations, frozen.
REMOTE_SCRIPT = Path('remote_script/Alberton_MCP/impl.py')
ALLOWED_OPS = frozenset({
    'ping', 'describe', 'expect', 'get', 'set', 'call',
    'get_notes', 'edit_notes', 'batch', 'subscribe', 'unsubscribe',
})


def remote_script_facts(source):
    """The `_op_*` methods and the value of the module-level HOST."""
    tree = ast.parse(source)
    ops = {node.name[len('_op_'):] for node in ast.walk(tree)
           if isinstance(node, ast.FunctionDef) and node.name.startswith('_op_')}
    host = None
    for node in tree.body:
        if (isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'HOST'
                                                 for t in node.targets)
                and isinstance(node.value, ast.Constant)):
            host = node.value.value
    return ops, host


def check_repo_rules(files):
    path = ROOT / REMOTE_SCRIPT
    if not path.exists():
        return [f'{REMOTE_SCRIPT}: not found; update REMOTE_SCRIPT in tools/check_rules.py.']
    ops, host = remote_script_facts(path.read_text(encoding='utf-8'))
    problems = []
    for op in sorted(ops - ALLOWED_OPS):
        problems.append(f'{REMOTE_SCRIPT}: new op "{op}". "The Remote Script never grows a '
                        'vocabulary" (CLAUDE.md): build the capability in the server. If it is '
                        'a genuine bridge fix the owner approved, add it to ALLOWED_OPS here '
                        'and to docs/CONTRACT.md.')
    for op in sorted(ALLOWED_OPS - ops):
        problems.append(f'{REMOTE_SCRIPT}: op "{op}" is gone. Update ALLOWED_OPS and '
                        'docs/CONTRACT.md together with the Remote Script.')
    if host != '127.0.0.1':
        problems.append(f'{REMOTE_SCRIPT}: HOST is {host!r}. "Bind to 127.0.0.1" (CLAUDE.md): '
                        'the socket carries unauthenticated control of the DAW.')
    return problems


def main():
    files = tracked_files()
    problems = check_domain_words(files) + check_english_in_code(files) + check_repo_rules(files)
    for p in problems:
        print(p)
    print(f'check_rules: {len(problems)} problem(s) in {len(files)} tracked files')
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())

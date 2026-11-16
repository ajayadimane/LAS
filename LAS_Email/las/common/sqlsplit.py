"""Split a SQL script into statements (handles comments, quotes and $$ bodies)."""

from __future__ import annotations


def split_sql(script: str) -> list[str]:
    stmts: list[str] = []
    buf: list[str] = []
    i, n = 0, len(script)
    in_single = False
    dollar: str | None = None
    while i < n:
        c = script[i]
        if dollar is not None:
            if script.startswith(dollar, i):
                buf.append(dollar)
                i += len(dollar)
                dollar = None
                continue
            buf.append(c)
            i += 1
            continue
        if in_single:
            buf.append(c)
            if c == "'":
                in_single = False
            i += 1
            continue
        if script.startswith("--", i):
            j = script.find("\n", i)
            i = n if j == -1 else j + 1
            buf.append("\n")
            continue
        if c == "'":
            in_single = True
            buf.append(c)
            i += 1
            continue
        if c == "$":
            j = script.find("$", i + 1)
            tag = script[i : j + 1] if j != -1 else ""
            if tag and all(ch.isalnum() or ch == "_" for ch in tag[1:-1]):
                dollar = tag
                buf.append(tag)
                i = j + 1
                continue
        if c == ";":
            stmt = "".join(buf).strip()
            if stmt:
                stmts.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        stmts.append(tail)
    return stmts

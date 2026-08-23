# -*- coding: utf-8 -*-
"""본문 문서(dict) 안을 조항 단위로 다루는 층.

★API 응답의 핵심 구조 (2026-08-05 실측으로 알아낸 것)

    `title` 은 그 항목 자신의 제목이 **아니다.** 그 항목이 속한 **조항의 제목**이,
    그 조항에 딸린 모든 항목에 똑같이 반복해 붙는다.

        label='4.2.1.1.3'  title='4.2.1.1.3 압축판요소의 폭두께비'   ← 조항 머리
        label='(1)'        title='4.2.1.1.3 압축판요소의 폭두께비'   ← 그 안의 문단
        label='표 4.2-2'   title='4.2.1.1.3 압축판요소의 폭두께비'   ← 그 안의 표

    그래서 "4.2.3 절을 읽어라" 는 `level` 을 따라 트리를 타는 게 아니라
    **title 의 앞머리 조항번호로 고르면 된다.** 훨씬 단순하고 안 깨진다.

`label` 의 종류: 조항번호(`4.2.3`) · 문단(`(1)` `①` `가.`) · 캡션(`표 4.2-2`) · `본문`.
"""
from __future__ import annotations

import re

from .render import has_formula_image, html_to_markdown

_CLAUSE_RE = re.compile(r"^\s*(\d+(?:\.\d+)*)\.?\s*")
_NUM_LABEL_RE = re.compile(r"^\d+(\.\d+)*\.?$")


def clause_no(title: str | None) -> str:
    """'4.2.1 일반규정 ' → '4.2.1'. 조항번호로 시작하지 않으면 ''."""
    m = _CLAUSE_RE.match(title or "")
    return m.group(1) if m else ""


def is_clause_head(item: dict) -> bool:
    return bool(_NUM_LABEL_RE.match((item.get("label") or "").strip()))


def items(doc: dict) -> list[dict]:
    return sorted(doc.get("list") or [], key=lambda x: x.get("sort") or 0)


def outline(doc: dict, depth: int | None = None) -> list[dict]:
    """목차 — 조항 머리 항목만. depth=2 면 `4.2` 까지, None 이면 전부."""
    out = []
    for it in items(doc):
        if not is_clause_head(it):
            continue
        no = (it.get("label") or "").strip().rstrip(".")
        if depth is not None and no.count(".") + 1 > depth:
            continue
        title = (it.get("title") or "").strip()
        out.append({"no": no, "title": title, "depth": no.count(".") + 1})
    return out


def in_section(item: dict, sec: str) -> bool:
    """항목이 `sec` 조항(또는 그 하위)에 속하는가."""
    no = clause_no(item.get("title"))
    if not no:
        return False
    return no == sec or no.startswith(sec + ".")


def section_items(doc: dict, sec: str) -> list[dict]:
    sec = (sec or "").strip().rstrip(".")
    return [it for it in items(doc) if in_section(it, sec)]


def render_items(its: list[dict], images: list | None = None) -> tuple[str, bool]:
    """항목들 → 마크다운. (본문, 수식이미지포함여부).

    images 를 주면 수식 이미지를 `(바이트, 형식)` 으로 모아 담고 본문에 〔그림 N〕 번호를 붙인다.
    번호는 항목을 넘나들며 이어진다.
    """
    out: list[str] = []
    saw_formula = False
    last_title = None
    for it in its:
        label = (it.get("label") or "").strip()
        title = (it.get("title") or "").strip()
        html = it.get("contents") or ""
        if has_formula_image(html):
            saw_formula = True
        if is_clause_head(it):
            no = clause_no(title) or label.rstrip(".")
            level = min(6, (no.count(".") if no else 0) + 2)
            out.append("#" * level + " " + (title or label))
            last_title = title
            continue
        body = html_to_markdown(html, images)
        if not body:
            continue
        # 조항 머리 없이 본문부터 나오는 경우(부분 조회) 맥락을 한 번 붙인다.
        if last_title is None and title:
            out.append(f"**{title}**")
            last_title = title
        # 캡션 라벨(`표 4.2-2`)은 표 안의 caption 과 겹치기 쉽다. 겹치면 덧붙이지 않는다.
        if label and label != "본문" and not body.lstrip("*").startswith(label):
            out.append(f"**[{label}]** {body}" if len(body) < 200 else f"**[{label}]**\n{body}")
        else:
            out.append(body)
    return "\n\n".join(x for x in out if x), saw_formula


def clauses(doc: dict) -> list[dict]:
    """문서를 **조항 단위로 묶는다** — {no, title, text}.

    `title` 이 같은 항목들이 곧 한 조항이므로 그것으로 묶는다.
    `text` 는 표까지 마크다운으로 편 본문 — 기준의 값은 대부분 표에 있어서
    표를 빼고 훑으면 정작 찾는 말을 놓친다.
    """
    groups: list[dict] = []
    index: dict[str, dict] = {}
    for it in items(doc):
        title = (it.get("title") or "").strip()
        if not title:
            continue
        g = index.get(title)
        if g is None:
            g = {"no": clause_no(title), "title": title, "parts": []}
            index[title] = g
            groups.append(g)
        if not is_clause_head(it):
            body = html_to_markdown(it.get("contents") or "")
            if body:
                g["parts"].append(body)
    for g in groups:
        g["text"] = "\n".join(g.pop("parts"))
    return groups


def snippets(text: str, needle: str, limit: int = 3, width: int = 70) -> list[str]:
    """찾은 말 앞뒤를 잘라 보여 준다. 어디에 있는지 눈으로 확인시키는 것이 목적이다."""
    out: list[str] = []
    start = 0
    low, low_needle = text.lower(), needle.lower()
    while len(out) < limit:
        i = low.find(low_needle, start)
        if i < 0:
            break
        a, b = max(0, i - width), min(len(text), i + len(needle) + width)
        frag = " ".join(text[a:b].split())
        out.append(("…" if a > 0 else "") + frag + ("…" if b < len(text) else ""))
        start = b   # 창 끝에서 다시 찾는다 — 안 그러면 거의 같은 토막이 겹쳐 나온다
    return out


def header(doc: dict) -> str:
    """출처 머리글 — 무엇을·언제 판을 봤는지 항상 같이 낸다."""
    return (
        f"# {doc.get('name')} ({doc.get('codeType')} {doc.get('code')})\n"
        f"*버전 {doc.get('version')} · 개정일 {str(doc.get('updateDate'))[:10]} · "
        f"출처 국가건설기준센터(KCSC)*"
    )

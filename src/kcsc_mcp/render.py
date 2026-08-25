# -*- coding: utf-8 -*-
"""KCSC 본문 HTML → 마크다운.

지키는 것 두 가지:
  · **표를 보존한다.** `<table>` 을 마크다운 표로 바꾼다. 기준의 값은 대부분 표에 있다.
  · **수식을 지어내지 않는다.** 수식·기호는 base64 이미지라 텍스트가 없다.
    〔그림 N〕 으로 자리와 번호만 표시한다. 없는 식을 추측해 넣지 않는다.

★수식 이미지에 대해 (2026-08-06 실측으로 바로잡은 것)

  오래 "수식은 못 읽는다"고 여겼으나, 정확히는 **텍스트로만 없고 그림으로는 멀쩡히 있다.**
  `alt` 도 MathML 도 없지만 GIF 자체는 또렷하다 — 식 한 줄도, 6×13px 기호도 읽힌다.
  이미지도 작아서(절당 27~216 비전토큰) 그대로 넘겨줄 만하다.

  그래서 `collect_images=True` 로 부르면 이미지를 **번호를 매겨 함께 꺼낸다.**
  받는 쪽이 진짜 기준의 진짜 식을 보게 된다 — 기억으로 채우는 구조 자체가 없어진다.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re

from bs4 import BeautifulSoup

#: 표를 본문에서 잠시 빼 둘 때 쓰는 자리표.
#: (숫자 마커를 쓰면 본문의 다른 숫자까지 표로 오인한다 — 실제로 겪은 버그다)
_MARK = "\x00TBL{}\x00"
_MARK_RE = re.compile(r"\x00TBL(\d+)\x00")

IMAGE_PLACEHOLDER = "〔그림〕"

#: 수식 그림 → 텍스트 사전. 열쇠는 **그림 내용의 sha256 앞 16자**다.
#  절 번호가 아니라 그림 자체로 잡기 때문에, 사전을 만들 때 훑지 않은 절에서도
#  같은 기호·같은 식이면 그대로 듣는다(원문이 같은 GIF 를 재사용한다 — 실측 2,664회/고유 1,317개).
#: 사전 파일의 자리. **저장소에는 넣지 않는다** — 기준 원문을 재배포하지 않는다는
#  LICENSE 의 KCSC 고지와 어긋나기 때문이다. 쓰려면 환경변수로 경로를 준다.
#  없으면 사전이 비고, 수식 자리는 지금까지처럼 〔그림 N〕 으로 남는다.
_GLOSSARY_PATH = os.getenv("KCSC_FORMULA_GLOSSARY") or os.path.join(
    os.path.dirname(__file__), "data", "formula_glossary.json")
_glossary: dict | None = None


def _load_glossary() -> dict:
    global _glossary
    if _glossary is None:
        try:
            with open(_GLOSSARY_PATH, encoding="utf-8") as f:
                _glossary = json.load(f)
        except (OSError, ValueError):
            _glossary = {}
    return _glossary


def formula_text(raw: bytes) -> str | None:
    """수식 그림의 바이트 → 옮겨 적은 텍스트. 모르는 그림이면 None.

    ★사전에 **없으면 지어내지 않는다.** 그 자리는 〔그림 N〕 으로 남고,
      `kcsc_formula` 로 원문 그림을 그대로 볼 수 있다. 그것이 안전선이다.
    ★확신이 덜한 판독에는 끝에 `⟨?⟩` 가 붙어 있다 — 그대로 내보내 사람이 알아채게 한다.
    """
    return _load_glossary().get(hashlib.sha256(raw).hexdigest()[:16])


def _clean(s: str | None) -> str:
    return re.sub(r"[ \t]+", " ", (s or "")).strip()


def _table_md(table) -> str:
    rows = []
    for tr in table.find_all("tr"):
        cells = []
        for td in tr.find_all(["td", "th"]):
            for img in td.find_all("img"):
                img.replace_with(IMAGE_PLACEHOLDER)
            cells.append(_clean(td.get_text(" ", strip=True)).replace("|", "\\|"))
        if any(c for c in cells):
            rows.append(cells)
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    out = []
    cap = table.find("caption")
    if cap:
        out.append(f"**{_clean(cap.get_text(' ', strip=True))}**")
    out.append("| " + " | ".join(rows[0]) + " |")
    out.append("| " + " | ".join(["---"] * width) + " |")
    for r in rows[1:]:
        out.append("| " + " | ".join(r) + " |")
    return "\n".join(out)


_DATA_URI_RE = re.compile(r"^data:image/(\w+);base64,(.+)$", re.S)


def decode_img(src: str | None) -> tuple[bytes, str] | None:
    """`data:image/gif;base64,…` → (바이트, 형식). 아니면 None."""
    m = _DATA_URI_RE.match((src or "").strip())
    if not m:
        return None
    try:
        return base64.b64decode(m.group(2)), m.group(1).lower()
    except (binascii.Error, ValueError):
        return None


#: `<sup>`/`<sub>` 를 글자로 바꾼다. 안 바꾸면 `mm` `2` `)` 로 줄이 쪼개져
#  `mm2)` 처럼 **차원이 틀린 것처럼 보인다**(원문은 mm²).
_SUPS = {"0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴",
         "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹",
         "+": "⁺", "-": "⁻", "n": "ⁿ"}
_SUBS = {"0": "₀", "1": "₁", "2": "₂", "3": "₃", "4": "₄",
         "5": "₅", "6": "₆", "7": "₇", "8": "₈", "9": "₉"}


def _fold_scripts(soup) -> None:
    """위·아래 첨자를 본문 글자로 접어 넣는다. 바꿀 글자가 없으면 `^`/`_` 로 적는다."""
    for tag, table, mark in (("sup", _SUPS, "^"), ("sub", _SUBS, "_")):
        for el in soup.find_all(tag):
            t = el.get_text("", strip=True)
            if t and all(c in table for c in t):
                el.replace_with(_TIGHT + "".join(table[c] for c in t) + _TIGHT)
            elif t:
                el.replace_with(_TIGHT + mark + t + _TIGHT)


#: 사전에서 온 글자를 감싸는 표시. 본문에 나올 리 없는 제어문자를 쓴다.
_INLINE = "\x01"
#: 붙여 쓸 것(첨자·닫는 괄호). _INLINE 은 한 칸 띄우고, 이건 붙인다.
_TIGHT = "\x02"
_TIGHT_LINE = re.compile(r"^\x02(.*)\x02$")
#: 이런 글자로 시작하는 줄은 앞줄에 붙는 꼬리다.
_TAIL = ")）,，·:";
_INLINE_LINE = re.compile(r"^\x01(.*)\x01$")
#: 앞줄이 이걸로 끝나면 **문장이 끝난 것**이므로 잇지 않는다.
_ENDS = ("다.", ".", ":", "：", "”", ")", "）")
#: 뒷줄이 조사로 시작하면 앞 식에 붙는 말이다 — 같이 잇는다.
_JOSA = re.compile(r"^(은|는|이|가|을|를|의|에|와|과|로|으로|도|만)(?=[ ,]|$)|^(은|는|이|가|을|를|의|에|와|과|로|도|만)[가-힣]")


def _joinable(prev: str) -> bool:
    """앞줄이 (표·머리글이 아닌) 보통 줄이고 문장으로 끝나지 않았으면 이어도 된다."""
    return bool(prev) and not prev.startswith(("#", "|", ">", "**[")) and not prev.endswith(_ENDS)


def _rejoin_inline(text: str) -> str:
    """식·기호가 제 줄로 떨어져 문장이 토막 난 것을 도로 잇는다.

    원문 HTML 이 식을 별도 노드로 두는 탓에 `get_text` 가 줄을 나눈다. 그대로 두면
    "공칭압축강도 / P_n / 은 … 산정한다." 처럼 읽을 수 없다.

    ★**앞줄이 문장으로 끝나지 않았을 때만** 잇는다 — 번호 붙은 독립 수식
      (`P_n = F_cr·A_g` 다음 줄에 `(4.2-1)`)은 제 줄에 그대로 남아야 한다.
    """
    out: list[str] = []
    for ln in text.split("\n"):
        m = _INLINE_LINE.match(ln)
        if m:
            body = m.group(1)
            if out and _joinable(out[-1]):
                out[-1] = out[-1] + " " + body
            else:
                out.append(body)
            continue
        # 첨자처럼 **붙여 써야** 하는 것 (`mm` + `²` → `mm²`)
        t = _TIGHT_LINE.match(ln)
        if t:
            if out and out[-1]:
                out[-1] = out[-1] + t.group(1)
            else:
                out.append(t.group(1))
            continue
        # 닫는 괄호만 남은 줄도 앞에 붙인다 (`(mm²` + `)`)
        if out and out[-1] and ln[:1] in _TAIL and len(ln) <= 2:
            out[-1] = out[-1] + ln
            continue
        # `여기서,` 목록은 기호와 설명이 딴 줄로 나온다 — `: …` 이면 앞 기호에 붙인다.
        if out and ln[:1] in ":：" and _joinable(out[-1]):
            out[-1] = out[-1] + " " + ln
            continue
        # 식 바로 다음 줄이 조사로 시작하면 그 식에 붙여 준다 ("… P_n" + "은 …")
        if out and _JOSA.match(ln) and _joinable(out[-1]):
            out[-1] = out[-1] + ln
            continue
        out.append(ln)
    return "\n".join(out).replace(_INLINE, "").replace(_TIGHT, "")



def html_to_markdown(html: str | None, images: list | None = None) -> str:
    """조항 하나의 contents(HTML) → 마크다운. 표는 표로, 이미지는 〔그림 N〕 으로.

    images 를 주면 그 자리의 이미지를 `(바이트, 형식)` 으로 **모아 담고 번호를 매긴다.**
    번호는 리스트 전체에 걸쳐 이어지므로, 여러 항목을 이어 붙여도 본문의 〔그림 N〕 과
    돌려준 이미지의 순서가 어긋나지 않는다.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    # ★이미지를 표보다 **먼저** 바꾼다. 표 안에도 수식 이미지가 들어 있어서(표 4.2-2 가
    #   통째로 그렇다), 표를 먼저 빼면 번호가 등장 순서와 어긋난다.
    _fold_scripts(soup)
    for img in soup.find_all("img"):
        got = decode_img(img.get("src"))
        # ★그림은 **번호를 매기는 것과 무관하게** 항상 사전을 먼저 본다.
        #   사전에 있으면 식을 글자로 그 자리에 넣는다 — 본문이 〔그림〕 도배가 되지 않는다.
        말 = formula_text(got[0]) if got else None
        # ★사전에서 온 글자에는 표시를 달아 둔다. 원문이 식을 별도 노드로 두는 탓에
        #   줄이 나뉘어 "공칭압축강도 / P_n / 은 …" 처럼 문장이 토막 난다.
        #   아래 _rejoin_inline 이 이 표시를 보고 도로 잇는다.
        if 말:
            말 = _INLINE + 말 + _INLINE
        if images is not None and got is not None:
            images.append(got)               # 번호는 그대로 이어 간다(kcsc_formula 와 맞춘다)
            img.replace_with(말 or f"〔그림 {len(images)}〕")
        else:
            img.replace_with(말 or IMAGE_PLACEHOLDER)
    tables: list[str] = []
    for t in soup.find_all("table"):
        tables.append(_table_md(t))
        t.replace_with(_MARK.format(len(tables) - 1))
    text = soup.get_text("\n", strip=True)
    text = _MARK_RE.sub(lambda m: "\n" + tables[int(m.group(1))] + "\n", text)
    text = "\n".join(_clean(ln) for ln in text.split("\n"))
    text = _rejoin_inline(text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def has_formula_image(html: str | None) -> bool:
    """수식 이미지가 들어 있는가. 있으면 '원문에서 식을 확인하라'고 붙인다."""
    return bool(html) and "<img" in html.lower()


def truncate(text: str, limit: int, hint: str = "") -> str:
    """상한을 넘으면 자르고 **잘렸다는 사실과 다음 행동**을 남긴다. 조용히 자르지 않는다."""
    if limit <= 0 or len(text) <= limit:
        return text
    cut = text[:limit].rsplit("\n", 1)[0]
    note = f"\n\n---\n⚠️ 출력이 길어 여기서 잘랐습니다 ({len(text):,}자 중 {len(cut):,}자)."
    if hint:
        note += f" {hint}"
    return cut + note

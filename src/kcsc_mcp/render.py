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
import re

from bs4 import BeautifulSoup

#: 표를 본문에서 잠시 빼 둘 때 쓰는 자리표.
#: (숫자 마커를 쓰면 본문의 다른 숫자까지 표로 오인한다 — 실제로 겪은 버그다)
_MARK = "\x00TBL{}\x00"
_MARK_RE = re.compile(r"\x00TBL(\d+)\x00")

IMAGE_PLACEHOLDER = "〔그림〕"


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


def html_to_markdown(html: str | None, images: list | None = None) -> str:
    """조항 하나의 contents(HTML) → 마크다운. 표는 표로, 이미지는 〔그림 N〕 으로.

    images 를 주면 그 자리의 이미지를 `(바이트, 형식)` 으로 **모아 담고 번호를 매긴다.**
    번호는 리스트 전체에 걸쳐 이어지므로, 여러 항목을 이어 붙여도 본문의 〔그림 N〕 과
    돌려준 이미지의 순서가 어긋나지 않는다.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    # ★이미지를 표보다 **먼저** 바꾼다. 표 안에도 수식 이미지가 들어 있어서(표 4.2-2 가
    #   통째로 그렇다), 표를 먼저 빼면 번호가 등장 순서와 어긋난다.
    for img in soup.find_all("img"):
        got = decode_img(img.get("src")) if images is not None else None
        if got is None:
            img.replace_with(IMAGE_PLACEHOLDER)
        else:
            images.append(got)
            img.replace_with(f"〔그림 {len(images)}〕")
    tables: list[str] = []
    for t in soup.find_all("table"):
        tables.append(_table_md(t))
        t.replace_with(_MARK.format(len(tables) - 1))
    text = soup.get_text("\n", strip=True)
    text = _MARK_RE.sub(lambda m: "\n" + tables[int(m.group(1))] + "\n", text)
    text = "\n".join(_clean(ln) for ln in text.split("\n"))
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

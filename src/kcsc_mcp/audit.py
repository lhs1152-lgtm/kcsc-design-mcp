# -*- coding: utf-8 -*-
"""계산 답변의 **인용 검증**.

★왜 필요한가 (2026-08-05, 실제 사례에서 나온 것)

  비정형 H형강 한계상태설계 답변이 `KDS 14 31 10 4.3.2.1.1.4` 를 근거로 φMn=83.8 kN·m 를
  냈다. 단면제원·Zx·Myc 를 검산해 보니 **전부 맞았다.** 그런데 그 절의 원문을 열어 보니
  식이 **하나도 텍스트로 없었다** — 전부 이미지였다. 즉 계산에 쓴 식과 계수(λp·kc·0.23 …)는
  기준을 읽어서 나온 게 아니라 **모델이 외운 것**이었다.

  이번엔 맞았다. 문제는 **맞았는지 틀렸는지 출력만 봐서는 구분이 안 된다**는 것이다.
  틀렸을 때도 표도, 조항 번호도, 출처 줄도 똑같이 생긴다.

  그래서 계산을 막는 대신 **추적 가능하게** 한다. 인용한 기준·조항·식 번호·표 번호가
  실재하는지 기계가 확인하고, 그 조항의 식이 이미지라서 도구가 못 읽었다는 사실을 밝힌다.

★이 검증이 하지 **못하는** 것 (반드시 함께 말해야 한다)
  · 식의 **내용**이 맞는지 — 원문이 이미지라 못 읽는다
  · 그 조항이 **이 부재·이 조건에 맞는지** — 판단의 영역이다
  · 계산이 맞는지
  확인되는 것은 "그 번호가 그 자리에 실재한다"는 사실뿐이다. 그 이상으로 읽으면
  이 도구가 새로운 거짓 안심을 만든다.
"""
from __future__ import annotations

import re

from . import client, config, doc as docmod

_TYPES = "|".join(config.CODE_TYPES)

#: `KDS 14 31 10` · `KDS 143110` (flows.py 와 같은 규칙 — 두 자리씩 끊어 읽는다)
_CODE_RE = re.compile(rf"\b({_TYPES})\s*(\d{{6}}(?:\d{{2}})?|\d{{2}}(?:\s+\d{{2}}){{2,3}})(?![\d.])", re.I)
#: `표 4.3-2` · `그림 4.2-1`
_CAPTION_RE = re.compile(r"(표|그림)\s*(\d+(?:\.\d+)*[-–]\d+[a-z]?)")
#: `(4.3-11)` · `식 4.3-12` · `4.3-16a`
_EQ_RE = re.compile(r"(?<![\d.])(\d+\.\d+[-–]\d+[a-z]?)(?![\d])")
#: 조항번호 후보. 값(1.68m·0.23)과 섞이므로 아래 규칙으로 걸러 낸다.
_CLAUSE_RE = re.compile(r"(?<![\d.\-])(\d+(?:\.\d+)+)(?![\d\-])")
#: 1단 조항(`4.3`)은 값(`0.23`)과 생김새가 같다. 그래서 단서가 있을 때만 조항으로 본다.
#: ★한국어는 단서가 숫자 **뒤**에 온다 — "4.3 절", "4.3 에 따른다". 앞만 보면 다 놓친다.
_CLAUSE_CUE_BEFORE = re.compile(r"(절|조항|항|따라|규정|기준|Section|section)\s*$")
_CLAUSE_CUE_AFTER = re.compile(r"^\s*(절|조항|항\b|호\b|에\s*따라|에\s*따른|을\s*따|참조|규정|의\s*규정)")
#: 값 뒤에 붙는 단위. ★한국어 조사와 겹치는 글자(도·배·개)를 넣으면 안 된다 —
#: `4.2.3 도 확인했다` 의 "도"를 단위로 보고 조항을 통째로 흘렸다.
_UNIT_AFTER = re.compile(r"^\s*(mm|cm|km|m\b|kN|MN|N\b|MPa|GPa|kg|ton|%|℃)")


def _strip_tags(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html or "")


def _index(doc: dict) -> dict:
    """문서 한 벌을 훑어 만든 색인 — 식 번호·표 번호가 **어느 절에** 있는지.

    bs4 를 태우지 않고 태그만 걷어 낸다(2,700항목을 도는 자리라 속도가 중요하다).
    """
    eqs: dict[str, set[str]] = {}
    caps: dict[str, set[str]] = {}
    img_clauses: set[str] = set()
    for it in docmod.items(doc):
        no = docmod.clause_no(it.get("title"))
        html = it.get("contents") or ""
        if "<img" in html.lower() and no:
            img_clauses.add(no)
        label = (it.get("label") or "").strip()
        m = _CAPTION_RE.match(label)
        if m:
            caps.setdefault(f"{m.group(1)} {m.group(2)}", set()).add(no)
        text = _strip_tags(html)
        for e in _EQ_RE.findall(text):
            eqs.setdefault(e, set()).add(no)
        for a, b in _CAPTION_RE.findall(text):
            caps.setdefault(f"{a} {b}", set()).add(no)
    return {"eqs": eqs, "caps": caps, "img": img_clauses}


def extract(text: str) -> list[dict]:
    """답변 글에서 인용을 뽑는다 → [{kind, code, ref}]

    기준이 여러 개면 **글에서 가장 가까운 앞쪽 기준**에 딸린 것으로 본다(어림짐작이다).
    `code` 로 하나를 지정하면 그것으로 통일한다.
    """
    s = text or ""
    codes = [(m.start(), f"{m.group(1).upper()} {re.sub(r'[^0-9]', '', m.group(2))}")
             for m in _CODE_RE.finditer(s)]

    def owner(pos: int) -> str:
        prev = [c for p, c in codes if p <= pos]
        return prev[-1] if prev else (codes[0][1] if codes else "")

    out: list[dict] = []
    seen: set[tuple] = set()

    def add(kind: str, ref: str, pos: int):
        key = (kind, owner(pos), ref)
        if key not in seen:
            seen.add(key)
            out.append({"kind": kind, "code": key[1], "ref": ref})

    masked = list(s)

    for m in _CAPTION_RE.finditer(s):
        add("표/그림", f"{m.group(1)} {m.group(2)}", m.start())
        for i in range(m.start(), m.end()):
            masked[i] = " "
    s2 = "".join(masked)

    for m in _EQ_RE.finditer(s2):
        add("식", m.group(1), m.start())
        for i in range(m.start(), m.end()):
            masked[i] = " "
    s3 = "".join(masked)

    code_ends = [m.end() for m in _CODE_RE.finditer(s)]
    for m in _CLAUSE_RE.finditer(s3):
        ref = m.group(1)
        after = s3[m.end():m.end() + 12]
        if _UNIT_AFTER.match(after):                    # 1.68 m · 5.73 m → 값이다
            continue
        if s3[max(0, m.start() - 1):m.start()] in ("=", "≤", "≥", "<", ">"):
            continue
        if ref.count(".") == 1:                         # 0.23 · 1.30 과 구별이 안 되니 단서를 본다
            near_code = any(0 <= m.start() - e <= 3 for e in code_ends)   # 코드 바로 뒤
            if not (near_code
                    or _CLAUSE_CUE_AFTER.match(after)
                    or _CLAUSE_CUE_BEFORE.search(s3[max(0, m.start() - 16):m.start()])):
                continue
        add("조항", ref, m.start())

    for _, c in codes:
        if not any(o["code"] == c for o in out):
            add("기준", "", s.find(c[:3]))
    return out


def _check_one(kind: str, ref: str, d: dict, idx: dict) -> tuple[bool, str, bool]:
    """한 기준 안에서 인용 하나를 확인한다 → (찾음, 설명, 수식이미지여부)"""
    if kind == "기준":
        return True, (f"{d.get('name')} · 버전 {d.get('version')} · "
                      f"개정 {str(d.get('updateDate'))[:10]}"), False
    if kind == "조항":
        its = docmod.section_items(d, ref)
        if not its:
            return False, "", False
        has_img = any("<img" in (i.get("contents") or "").lower() for i in its)
        detail = (its[0].get("title") or "").strip()
        return True, detail + ("  ⚠️수식이미지" if has_img else ""), has_img
    table = idx["eqs"] if kind == "식" else idx["caps"]
    where = sorted(table.get(ref, []))
    return (True, f"{', '.join(where)} 절에 있음", False) if where else (False, "", False)


def audit(text: str, code_hint: str = "") -> dict:
    """인용을 하나씩 확인한다 → {rows, codes, notes}

    ★인용된 기준이 여럿이면 **모든 기준을 다 뒤진다.**
      "글에서 가장 가까운 앞쪽 기준"만 보면 틀린다 — 실제로 `KDS 14 31 05` 가 앞에 있다는
      이유로 `식 4.3-11`(실제로는 KDS 14 31 10 것)을 없다고 판정했다.
      **거짓 경보는 이 도구를 못 믿게 만든다.** 어디에도 없을 때만 ❌ 로 낸다.
    """
    cites = extract(text)
    codes = list(dict.fromkeys(c["code"] for c in cites if c["code"]))
    if code_hint:
        t, digits = client.normalize_code(code_hint)
        forced = f"{t or ''} {digits}".strip()
        codes = [forced] + [c for c in codes if c != forced]

    docs: dict[str, tuple] = {}
    rows: list[dict] = []
    notes: list[str] = []

    def load(code: str):
        if code not in docs:
            try:
                d, _ = client.document(code)
                docs[code] = (d, _index(d))
            except client.KcscError as e:
                docs[code] = (None, str(e).splitlines()[0])
        return docs[code]

    seen: set[tuple] = set()
    for c in cites:
        kind, ref = c["kind"], c["ref"]
        if (kind, ref) in seen:
            continue
        seen.add((kind, ref))
        # 딸린 것으로 본 기준을 먼저, 그 다음 글에 나온 나머지 기준을 전부 뒤진다
        order = [c["code"]] + [x for x in codes if x != c["code"]] if c["code"] else codes
        if not order:
            rows.append({**c, "ok": False, "detail": "어느 기준의 것인지 알 수 없습니다"})
            continue
        found = None
        errs = []
        for code in order:
            d, idx = load(code)
            if d is None:
                errs.append(f"{code}: {idx}")
                continue
            ok, detail, img = _check_one(kind, ref, d, idx)
            if ok:
                found = {**c, "code": code, "ok": True, "detail": detail, "img": img}
                break
        if found:
            if found["code"] != c["code"] and c["code"]:
                found["detail"] += f"  (글에서는 {c['code']} 쪽에 붙어 있었습니다)"
            rows.append(found)
        else:
            where = " · ".join(order)
            rows.append({**c, "ok": False,
                         "detail": (errs[0] if errs and len(order) == len(errs)
                                    else f"인용된 기준({where}) 어디에도 없습니다")})

    img_clauses = sorted({r["ref"] for r in rows if r["kind"] == "조항" and r.get("img")})
    if img_clauses:
        notes.append(
            "★" + ", ".join(img_clauses) + " 절의 **식은 원문이 이미지**입니다. 도구가 읽지 못했습니다.\n"
            "  → 이 계산에 쓰인 식·계수는 **원문에서 온 것이 아니라 모델이 채운 것**입니다.\n"
            "  → 값을 쓰기 전에 설계자가 원문에서 식을 대조해야 합니다.")
    return {"rows": rows, "notes": notes, "codes": list(docs)}


LIMITS = (
    "> **이 검증이 확인한 것은 「그 번호가 그 자리에 실재한다」는 사실뿐입니다.**\n"
    "> 확인하지 **못한** 것: 식의 내용이 맞는지(원문이 이미지) · 그 조항이 이 부재·이 조건에\n"
    "> 맞는지(판단의 영역) · 계산이 맞는지. **최종판단은 설계자가 합니다.**"
)

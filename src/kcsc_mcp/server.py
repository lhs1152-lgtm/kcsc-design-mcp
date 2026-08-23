# -*- coding: utf-8 -*-
"""kcsc-mcp — 국가건설기준 원문 조회 MCP 서버.

★안전선 (도구 설명에도 박아 둔다)
    이 서버는 **기준 원문을 그대로 가져다 보여 주는 것**까지만 한다.
    구조계산을 대신하지 않는다. 수식·기호는 원문이 이미지라 텍스트로 존재하지 않으므로
    〔그림〕 으로 자리만 표시한다. **값·식·최종판단은 설계자의 몫이다.**
    교량 하중 하나가 틀리면 인명 사고다. 그 선을 코드로 지킨다.
"""
from __future__ import annotations

import re
from pathlib import Path

from . import __version__, audit as auditmod, client, config, doc as docmod, flows, sheet
from .render import truncate

# MCP SDK 2.0 에서 `FastMCP` 가 `MCPServer` 로 옮겨졌다. 데코레이터 API 는 같다.
# 남이 설치해 쓰는 패키지라 어느 쪽 SDK 에도 붙게 둔다.
try:
    from mcp.server import MCPServer as _ServerClass  # mcp >= 2.0
    from mcp.server.mcpserver import Image
    mcp = _ServerClass("kcsc", version=__version__)
except ImportError:  # pragma: no cover - mcp 1.x
    from mcp.server.fastmcp import FastMCP as _ServerClass, Image
    mcp = _ServerClass("kcsc")

_SAFETY = (
    "\n\n---\n"
    "> 위는 국가건설기준 **원문 인용**입니다. 수식·기호는 원문이 이미지라 〔그림〕 으로 표시되며, "
    "실제 식과 값은 원문에서 확인해야 합니다. **최종판단은 설계자가 합니다.**"
)

_FORMULA_NOTE = (
    "\n\n> ⚠️ 이 절의 **수식은 원문이 이미지**라 위 본문에 `〔그림 N〕` 으로만 나옵니다.\n"
    "> **식이 필요하면 `kcsc_formula` 로 그림을 그대로 받으세요** — 번호가 같습니다.\n"
    "> 그림을 안 보고 계산하면 그 식은 원문이 아니라 **기억에서 나온 것**입니다. "
    "그 경우 사실을 밝히고 `kcsc_audit` 으로 인용을 검증하세요."
)


def _err(e: Exception) -> str:
    return f"❌ {e}"


def _norm_txt(s: str) -> str:
    return re.sub(r"\s+", "", str(s or "")).lower()


@mcp.tool()
def kcsc_search(query: str, code_type: str = "", limit: int = 20, domain: str = "") -> str:
    """국가건설기준을 이름으로 찾는다 (KDS 설계기준·KCS 표준시방서 등 3,572건).

    query: 찾을 말. 띄어쓰기로 나눈 낱말이 **모두** 들어간 기준을 찾는다. (예: "강구조 부재")
    code_type: KDS·KCS·SMCS·LHCS·EXCS·KRCCS·KWCS·NHCS·KRACS 중 하나로 좁힌다. 빈 값이면 전체.
    limit: 최대 건수.
    domain: 분야. **비우면 `교량` 이 기본**이다 (건축 기준이 딸려 오는 것을 막기 위해).
            건축구조물이면 `건축` 이라고 지정한다. `전체` 로 두면 안 가린다.

    ★분야를 밝히지 않으면 **교량으로 봅니다.** 결과에 분야를 표시하고, 기본 분야가 아닌 것은
      뒤로 미룹니다 — 교량 설계에 건축 기준(KDS 14 3x)을 쓰면 하중조합부터 달라집니다.

    ※ 이 검색은 **기준 이름과 상위 분류**만 본다. 본문 속 낱말은 찾지 못한다
      (예: "강관"은 본문에 있어도 이름에는 없다). 본문 검색은 `kcsc_grep` 을 쓴다.
    """
    try:
        entries = client.catalog()
    except client.KcscError as e:
        return _err(e)

    want_type = (code_type or "").strip().upper()
    if want_type and want_type not in config.CODE_TYPES:
        return _err(Exception(f"모르는 기준 종류 {want_type!r}. 가능: {' · '.join(config.CODE_TYPES)}"))
    if want_type:
        entries = [e for e in entries if str(e.get("codeType", "")).upper() == want_type]

    tokens = [t for t in re.split(r"\s+", (query or "").strip()) if t]
    if not tokens:
        return _err(Exception("찾을 말을 넣어 주세요. 예: `강구조 부재`"))

    hits = []
    for e in entries:
        hay = " ".join([str(e.get("name") or ""), str(e.get("code") or ""),
                        *client.parent_names(e)])
        if all(t in hay for t in tokens):
            hits.append(e)

    if not hits:
        return (f"'{query}' 에 맞는 기준을 찾지 못했습니다.\n"
                "· 낱말을 줄여 보세요 (모든 낱말이 다 들어가야 걸립니다).\n"
                "· 이름에 없고 **본문에만** 있는 말일 수 있습니다 → 기준을 좁힌 뒤 `kcsc_grep` 을 쓰세요.")

    # ★분야 정렬 — 기본 분야(교량)를 앞에, 다른 분야는 뒤로. 가리지는 않되 순서로 알린다.
    dom = flows.resolve_domain(domain)
    show_all = _norm_txt(domain) in ("전체", "all")
    for e in hits:
        e["_분야"] = config.domain_of_code(e.get("code"))
    if not show_all:
        hits.sort(key=lambda e: (e["_분야"] != dom, str(e.get("codeType")), str(e.get("code"))))
    else:
        hits.sort(key=lambda e: (str(e.get("codeType")), str(e.get("code"))))

    shown = hits[:max(1, limit)]
    other = [e for e in shown if e["_분야"] != dom]
    lines = [f"**'{query}'** — {len(hits)}건" + (f" (앞 {len(shown)}건만 표시)" if len(hits) > len(shown) else "")]
    if not show_all:
        lines.append(f"*분야 기본값 **{dom}** — 다른 분야는 뒤에 놓았습니다. "
                     f"건축구조물이면 `domain='건축'`, 안 가리려면 `domain='전체'`*")
    lines += ["", "| 분야 | 종류 | 코드 | 이름 | 버전 | 개정일 |", "| --- | --- | --- | --- | --- | --- |"]
    for e in shown:
        mark = "✅" if e["_분야"] == dom else "· "
        lines.append(f"| {mark}{e['_분야']} | {e.get('codeType')} | {e.get('code')} | {e.get('name')} | "
                     f"{e.get('version')} | {str(e.get('updateDate'))[:10]} |")
    lines.append("")
    if other and not show_all:
        lines.append(f"> ⚠️ 위에 **{dom} 이 아닌 기준 {len(other)}건**이 섞여 있습니다. "
                     f"{dom} 설계에 다른 분야 기준을 쓰면 하중조합부터 달라집니다.")
    lines.append("→ 목차는 `kcsc_outline`, 원문은 `kcsc_read` 로 봅니다.")
    return "\n".join(lines)


@mcp.tool()
def kcsc_outline(code: str, code_type: str = "", depth: int = 3) -> str:
    """기준의 목차(조항번호 계층)를 낸다. 어느 절을 읽을지 고르는 데 쓴다.

    code: `KDS 14 31 10` · `14 31 10` · `143110` 다 받는다.
    code_type: 6자리 코드는 종류가 다르면 겹칠 수 있다. 겹치면 여기에 KDS·KCS 등을 지정한다.
    depth: 몇 단계까지 볼지. 3이면 `4.2.1` 까지. 0이나 음수면 전부.
    """
    try:
        d, note = client.document(code, code_type or None)
    except client.KcscError as e:
        return _err(e)

    lim = None if depth is None or depth <= 0 else depth
    rows = docmod.outline(d, depth=lim)
    if not rows:
        return f"{d.get('codeType')} {d.get('code')} 에서 조항 목차를 찾지 못했습니다."

    total = len(docmod.outline(d))
    lines = [docmod.header(d), ""]
    if note:
        lines += [note, ""]
    for r in rows:
        lines.append("  " * (r["depth"] - 1) + f"- {r['title']}")
    lines.append("")
    lines.append(f"*{len(rows)}개 표시 / 전체 조항 {total}개*"
                 + (f" — 더 깊이 보려면 `depth` 를 올리세요." if lim and total > len(rows) else ""))
    lines.append("→ 원문은 `kcsc_read(code, section)` 으로 봅니다. 예: `kcsc_read('KDS 14 31 10', '4.2.3')`")
    return truncate("\n".join(lines), config.max_chars(), "`depth` 를 줄여 보세요.")


@mcp.tool()
def kcsc_read(code: str, section: str = "", code_type: str = "", max_chars: int = 0) -> str:
    """기준 원문을 절 단위로 읽는다. **표는 표 그대로 보존**된다.

    code: `KDS 14 31 10` · `143110` 등.
    section: 조항번호. `4.2` 를 주면 `4.2.x` 하위까지 전부 포함한다. 빈 값이면 문서 전체
             (대개 매우 길다 — 먼저 `kcsc_outline` 으로 절을 고르는 편이 낫다).
    code_type: 코드가 겹칠 때만 지정.
    max_chars: 출력 상한. 0이면 기본값(KCSC_MAX_CHARS, 기본 20000).

    ※ 수식·기호는 원문이 이미지라 〔그림〕 으로만 나온다. **이 도구는 식을 지어내지 않는다.**
      이 원문을 근거로 계산했다면 식은 원문이 아니라 기억에서 온 것이므로,
      그 사실을 밝히고 `kcsc_audit` 으로 인용을 검증할 것.
    """
    try:
        d, note = client.document(code, code_type or None)
    except client.KcscError as e:
        return _err(e)

    sec = (section or "").strip().rstrip(".")
    if sec:
        its = docmod.section_items(d, sec)
        if not its:
            avail = ", ".join(r["no"] for r in docmod.outline(d, depth=2)[:20])
            return (f"{d.get('codeType')} {d.get('code')} 에 `{sec}` 절이 없습니다.\n"
                    f"있는 절(2단계까지): {avail}\n"
                    "`kcsc_outline` 로 목차를 확인해 주세요.")
    else:
        its = docmod.items(d)

    # 이미지를 실제로 돌려주진 않지만 **번호는 매긴다** — `kcsc_formula` 가 돌려줄 이미지와
    # 번호가 같아야 "〔그림 12〕가 무슨 식인지" 물을 수 있다.
    numbering: list = []
    body, saw_formula = docmod.render_items(its, numbering)
    head = docmod.header(d)
    if note:
        head += "\n\n" + note
    if sec:
        head += f"\n\n**조회 범위: {sec} 절**"
    text = head + "\n\n" + body
    if saw_formula:
        text += _FORMULA_NOTE
    text += _SAFETY

    limit = max_chars if max_chars and max_chars > 0 else config.max_chars()
    hint = ("`section` 을 더 좁혀 주세요 (예: '4.2.3')." if not sec
            else "하위 절을 하나씩 읽어 주세요.")
    return truncate(text, limit, hint)


#: 한 번의 grep 이 받아올 수 있는 문서 수. 문서 하나가 수 MB 라 무한정 늘릴 수 없다.
_GREP_MAX_DOCS = 10

#: 한 번에 돌려줄 수식 이미지 수의 기본 상한.
#: 이미지 자체는 작지만(절당 27~216 비전토큰) 개수가 많으면 클라이언트가 버거워한다.
#: KDS 14 31 10 의 4.3.2.1.1.4 는 한 절에 71개다.
_IMG_DEFAULT_MAX = 40


@mcp.tool()
def kcsc_formula(code: str, section: str, code_type: str = "", max_images: int = 0) -> list:
    """★그 절의 **수식을 이미지 그대로** 가져온다. 식을 기억으로 채우지 않아도 된다.

    code: 기준 코드 (`KDS 14 31 10` 등)
    section: 조항번호. **반드시 좁혀서 지정한다** (예: `4.3.2.1.1.4`). 한 절에 이미지가
             수십 개다.
    code_type: 코드가 겹칠 때만 지정.
    max_images: 최대 이미지 수. 0이면 기본 40.

    KCSC 원문의 수식·기호는 텍스트가 아니라 **GIF 이미지**입니다 (`alt` 도 MathML 도 없음).
    이 도구는 그 이미지를 그대로 돌려줍니다 — 본문의 `〔그림 N〕` 과 **번호가 같습니다.**

    ※ 이미지를 읽는 것도 인식이라 **첨자를 잘못 볼 수 있습니다.** 다만 설계자가 같은 그림을
      볼 수 있어 대조가 됩니다. 기억으로 채운 식은 대조할 대상조차 없습니다.
      **최종판단은 설계자가 합니다.**
    """
    sec = (section or "").strip().rstrip(".")
    if not sec:
        return ["❌ 조항번호를 지정해 주세요. 한 절에 이미지가 수십 개라 문서 전체는 받지 않습니다.\n"
                "먼저 `kcsc_outline` 으로 절을 고르세요."]
    try:
        d, note = client.document(code, code_type or None)
    except client.KcscError as e:
        return [_err(e)]
    its = docmod.section_items(d, sec)
    if not its:
        return [f"{d.get('codeType')} {d.get('code')} 에 `{sec}` 절이 없습니다. "
                "`kcsc_outline` 로 목차를 확인해 주세요."]

    images: list = []
    body, _ = docmod.render_items(its, images)
    if not images:
        return [f"{docmod.header(d)}\n\n**{sec} 절에는 수식 이미지가 없습니다.** "
                f"본문은 `kcsc_read` 로 보세요."]

    limit = max_images if max_images and max_images > 0 else _IMG_DEFAULT_MAX
    shown = images[:limit]
    head = [docmod.header(d)]
    if note:
        head.append(note)
    head.append(f"**{sec} 절의 수식 이미지 {len(images)}개**"
                + (f" — 앞 {len(shown)}개만 보냅니다 (`max_images` 로 조절)" if len(shown) < len(images) else ""))
    head.append("아래 이미지는 본문의 `〔그림 1〕`·`〔그림 2〕`… 와 **번호 순서가 같습니다.**")
    head.append("")
    head.append(truncate(body, 4000, "본문 전체는 `kcsc_read` 로 보세요."))
    head.append("")
    head.append("> 위 이미지는 국가건설기준 **원문 그대로**입니다. 읽은 식을 옮길 때 첨자를 "
                "잘못 볼 수 있으니 설계자가 대조하세요. **최종판단은 설계자가 합니다.**")

    out: list = ["\n\n".join(x for x in head if x)]
    for data, fmt in shown:
        out.append(Image(data=data, format=fmt))
    return out


def _split_codes(code: str) -> list[str]:
    """쉼표로 나눈 코드 목록. 같은 것을 두 번 세지 않도록 순서를 지키며 중복을 지운다."""
    out, seen = [], set()
    for c in re.split(r"[,\n;]+", code or ""):
        c = c.strip()
        key = re.sub(r"\s+", "", c).upper()
        if c and key not in seen:
            seen.add(key)
            out.append(c)
    return out


@mcp.tool()
def kcsc_grep(code: str, keyword: str, code_type: str = "", limit: int = 20) -> str:
    """기준 **본문**에서 그 말이 있는 절을 찾는다. `kcsc_search` 가 못 보는 곳을 본다.

    code: 기준 코드. **쉼표로 여러 개**를 줄 수 있다 (예: `KDS 14 31 10, KDS 14 31 05`).
          문서를 통째로 받아 훑기 때문에 한 번에 최대 10건까지만 받는다.
    keyword: 찾을 말. 띄어쓰기로 나눈 낱말이 **모두** 들어간 절을 찾는다.
    code_type: 코드가 겹칠 때만 지정.
    limit: 최대 절 수.

    표 안의 글자도 함께 찾는다 — 기준의 값은 대부분 표에 있기 때문이다.
    수식은 원문이 이미지라 **찾을 수 없다** (〔그림〕 자리). 기호로는 검색되지 않는다.
    """
    codes = _split_codes(code)
    if not codes:
        return _err(Exception("기준 코드를 넣어 주세요. 예: `KDS 14 31 10`"))
    tokens = [t for t in re.split(r"\s+", (keyword or "").strip()) if t]
    if not tokens:
        return _err(Exception("찾을 말을 넣어 주세요. 예: `강관`"))

    dropped = codes[_GREP_MAX_DOCS:]
    codes = codes[:_GREP_MAX_DOCS]

    blocks: list[str] = []
    total = 0
    seen_docs: set[tuple[str, str]] = set()
    for one in codes:
        try:
            d, note = client.document(one, code_type or None)
        except client.KcscError as e:
            blocks.append(f"### {one}\n{_err(e)}")
            continue
        # `143110` 과 `KDS 14 31 10` 은 같은 문서다 — 두 번 세지 않는다
        key = (str(d.get("codeType")), str(d.get("code")))
        if key in seen_docs:
            continue
        seen_docs.add(key)
        found = []
        for c in docmod.clauses(d):
            text = c["text"]
            if not all(t in text for t in tokens):
                continue
            found.append((c, docmod.snippets(text, tokens[0])))
        total += len(found)
        title = f"### {d.get('codeType')} {d.get('code')} — {d.get('name')}"
        if not found:
            blocks.append(f"{title}\n(없음)")
            continue
        lines = [title]
        if note:
            lines.append(note)
        for c, snips in found[:max(1, limit)]:
            lines.append(f"\n**{c['title']}**" + (f"  → `kcsc_read` section=`{c['no']}`" if c["no"] else ""))
            for s in snips:
                lines.append(f"  - {s}")
        if len(found) > limit:
            lines.append(f"\n*{len(found)}개 중 {limit}개만 표시 — `limit` 을 올리세요.*")
        blocks.append("\n".join(lines))

    head = f"**'{keyword}'** — 본문 검색, 기준 {len(seen_docs)}건에서 절 {total}개"
    if dropped:
        head += f"\n\n> ⚠️ 한 번에 {_GREP_MAX_DOCS}건까지만 봅니다. **빠진 코드: {', '.join(dropped)}** — 나눠서 다시 불러 주세요."
    if total == 0:
        head += ("\n\n· 낱말을 줄여 보세요 (모든 낱말이 다 들어가야 걸립니다).\n"
                 "· 수식·기호(λr·Fcr 등)는 원문이 이미지라 검색되지 않습니다.")
    return truncate(head + "\n\n" + "\n\n".join(blocks), config.max_chars(),
                    "`limit` 을 줄이거나 기준을 하나씩 보세요.")


@mcp.tool()
def kcsc_audit(text: str, code: str = "") -> str:
    """계산·검토 답변의 **인용을 기계로 검증한다.** 기준·조항·식 번호·표 번호가 실재하는지 확인.

    text: 검증할 답변 글 전체를 그대로 넣는다. 안에서 인용을 뽑아 하나씩 확인한다.
    code: 기준이 하나뿐인데 글에 안 적혀 있으면 여기에 지정한다.

    ★**구조계산 답변을 냈으면 이 검증을 함께 돌리고 결과를 밝히세요.**
      KCSC 원문은 수식이 **이미지**라 도구가 읽지 못합니다. 그래서 계산에 쓴 식·계수는
      원문에서 온 것이 아니라 **모델이 기억으로 채운 것**입니다. 맞을 때도 있고 틀릴 때도
      있는데, **출력만 봐서는 구분이 안 됩니다.** 이 도구는 그 경계를 드러냅니다.

    확인하는 것: 기준 실재·버전 · 조항 실재 · **식 번호 실재와 그 식이 몇 절에 있는지** ·
                 표/그림 번호 실재 · **그 조항의 식이 이미지인지**
    확인하지 못하는 것: 식의 내용 · 그 조항이 이 부재에 맞는지 · 계산이 맞는지
    """
    if not (text or "").strip():
        return _err(Exception("검증할 글을 넣어 주세요. 계산 답변 전체를 그대로 붙이면 됩니다."))
    try:
        res = auditmod.audit(text, code)
    except client.KcscError as e:
        return _err(e)
    rows = res["rows"]
    if not rows:
        return ("글에서 기준 인용을 찾지 못했습니다.\n"
                "`KDS 14 31 10` 같은 기준 코드가 들어 있어야 확인할 수 있습니다. "
                "`code` 로 기준을 지정해도 됩니다.")

    ok = sum(1 for r in rows if r["ok"])
    bad = [r for r in rows if not r["ok"]]
    head = [f"## 인용 검증 — {len(rows)}건 중 **{ok}건 확인 · {len(bad)}건 실패**", ""]
    if bad:
        head.append("**❌ 확인 실패**")
        for r in bad:
            label = f"{r['code']} {r['ref']}".strip()
            head.append(f"- `{label}` — {r['detail']}")
        head.append("")

    order = {"기준": 0, "조항": 1, "식": 2, "표/그림": 3}
    lines = ["| 종류 | 기준 | 인용 | 확인 |", "| --- | --- | --- | --- |"]
    for r in sorted(rows, key=lambda x: (order.get(x["kind"], 9), x["ref"])):
        lines.append(f"| {r['kind']} | {r['code']} | {r['ref'] or '—'} | "
                     f"{'✅' if r['ok'] else '❌'} {r['detail']} |")

    tail = [""]
    if res["notes"]:
        tail += res["notes"] + [""]
    tail.append(auditmod.LIMITS)
    return truncate("\n".join(head + lines + tail), config.max_chars(), "글을 나눠서 검증하세요.")


@mcp.tool()
def kcsc_version(code: str, code_type: str = "") -> str:
    """기준의 버전·개정일을 확인한다. **개정 여부를 확인할 때 쓴다.**

    code: 기준 코드. 쉼표로 여러 개를 줄 수 있다.
    code_type: 코드가 겹칠 때만 지정.

    카탈로그를 새로 받아 확인하므로, 결정트리의 근거 조항을 다시 검증해야 하는지
    판단하는 데 쓸 수 있다.
    """
    codes = _split_codes(code)
    if not codes:
        return _err(Exception("기준 코드를 넣어 주세요. 예: `KDS 14 31 10`"))
    try:
        client.catalog(refresh=True)   # 개정 확인이 목적이니 캐시를 믿지 않는다
    except client.KcscError as e:
        return _err(e)

    lines = ["| 종류 | 코드 | 이름 | 버전 | 개정일 |", "| --- | --- | --- | --- | --- |"]
    notes: list[str] = []
    seen: set[tuple[str, str]] = set()
    for one in codes:
        try:
            entry, note = client.resolve(one, code_type or None)
        except client.KcscError as e:
            notes.append(_err(e))
            continue
        key = (str(entry.get("codeType")), str(entry.get("code")))
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"| {entry.get('codeType')} | {entry.get('code')} | {entry.get('name')} | "
                     f"{entry.get('version')} | {str(entry.get('updateDate'))[:10]} |")
        if note:
            notes.append(note)
        parents = client.parent_names(entry)
        if parents:
            notes.append(f"> `{entry.get('codeType')} {entry.get('code')}` 분류: " + " › ".join(parents))
    out = "\n".join(lines)
    if notes:
        out += "\n\n" + "\n".join(notes)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 설계 보조 — 결정트리
#
# ★안전선을 코드로 강제하는 곳이다.
#   · 검증 안 된 트리(draft·예제)에는 경고를 반드시 붙인다
#   · 설계법이 어긋나면 흐름을 내주지 않는다 (같은 부재라도 설계법이 다르면 기준이 다르다)
#   · 빈 엑셀에 계산식을 넣지 않는다 (원문이 이미지라 식을 알 수 없다)
#   · 근거로 적은 조항·식이 실재하는지 기계가 확인한다 (`design_validate`·`kcsc_audit`)
# ─────────────────────────────────────────────────────────────────────────────

_NO_TREE = (
    "쓸 수 있는 결정트리가 없습니다.\n"
    "`design_flows()` 로 목록을 보고, 없으면 `design_template()` 으로 뼈대를 받아\n"
    f"`{config.flows_dir()}` 에 YAML 로 넣으세요."
)


def _domain_banner(domain: str, method: str) -> str:
    return (f"> ℹ️ **분야를 밝히지 않아 `{domain}` 으로 봤습니다** (기본값). "
            f"그래서 설계법은 `{method}` 입니다.\n"
            "> **건축구조물이면 `domain='건축'` 을 지정하세요** — 교량과 건축은 기준 계열이\n"
            "> 통째로 다릅니다 (교량 KDS 24 14 31 / 건축 KDS 14 31 10). 하중조합부터 다릅니다.")


def _pick(member: str, shape: str, method: str,
          domain: str = "") -> tuple[dict | None, str, str]:
    """트리 하나를 고른다. → (트리, 오류문, 알림).

    ★분야를 밝히지 않으면 **교량**으로 본다.
      건축 기준이 딸려 오는 것을 막기 위한 기본값이다. 설계법을 안 주면 분야의 기본
      설계법을 쓰고, **그 설계법 트리가 없으면 다른 설계법으로 갈아타지 않고 없다고 답한다.**
    """
    if not (member or "").strip():
        return None, _err(Exception("부재를 넣어 주세요. 예: `압축부재`")), ""

    dom = flows.resolve_domain(domain)
    banner = ""
    if not (method or "").strip():
        method = flows.method_for_domain(dom)
        # 분야를 **말하지 않았을 때만** 기본값을 썼다고 알린다. 지정했으면 알릴 게 없다.
        if method and not (domain or "").strip():
            banner = _domain_banner(dom, method)
    hits = flows.find(member, shape, method)
    if not hits:
        near = flows.find(member)
        if near:
            opts = "\n".join(f"  · {t['부재']} / {t.get('단면')} / **{t.get('설계법')}** "
                             f"({flows.domain_of_tree(t)})" for t in near)
            head = (f"찾는 조건의 트리가 없습니다 — **{dom} / {method or '설계법 미지정'}** "
                    f"({member} / {shape or '단면 미지정'}).\n")
            if banner:
                head += f"\n{banner}\n"
            return None, (head + f"\n`{member}` 로 있는 트리:\n{opts}\n\n"
                          "★분야·설계법이 다르면 **다른 트리가 필요합니다.** 없는 것을 있는 것처럼\n"
                          "내주지 않습니다. 새로 만들려면 `design_template` 로 뼈대를 받으세요."), ""
        return None, f"조건에 맞는 트리가 없습니다 ({member} / {shape or '-'} / {method or '-'}).\n\n{_NO_TREE}", ""
    if len(hits) > 1:
        opts = "\n".join(f"  · {t['부재']} / {t.get('단면')} / {t.get('설계법')}" for t in hits)
        return None, ("조건에 맞는 트리가 여럿입니다. 단면까지 좁혀 주세요.\n" + opts), ""
    return hits[0], "", banner


@mcp.tool()
def design_flows() -> str:
    """쓸 수 있는 설계 결정트리 목록. 부재·단면·**설계법**·검증상태를 함께 낸다.

    트리는 두 곳에서 읽는다 — 패키지 동봉 예제와 사용자 폴더(`~/.kcsc-mcp/flows/*.yaml`).
    같은 조건이면 **사용자 폴더가 이긴다** (각 회사의 트리가 그 회사 기준이다).
    """
    trees = flows.load_all()
    if not trees:
        return _NO_TREE
    lines = ["| 부재 | 단면 | 설계법 | 근거 | 검증 | 출처 |",
             "| --- | --- | --- | --- | --- | --- |"]
    for t in sorted(trees, key=lambda x: (str(x.get("부재")), str(x.get("단면")))):
        v = str(t.get("검증") or "draft")      # 표시는 파일에 적힌 값 그대로
        mark = "✅" if flows.is_confirmed(t) else "⚠️"   # 판정만 정규화(옛 이름 포함)
        lines.append(f"| {t.get('부재')} | {t.get('단면')} | {t.get('설계법')} | {t.get('근거')} | "
                     f"{mark} {v} | {'동봉 예제' if t.get('_동봉') else '사용자'} |")
    lines += ["",
              f"사용자 트리 폴더: `{config.flows_dir()}`",
              "→ 흐름은 `design_flow`, 빈 엑셀은 `design_sheet`, 새 트리 뼈대는 `design_template`.",
              "",
              f"> ⚠️ `검증` 이 `{flows.VERIFY_OK}` 이 아닌 트리는 설계자 확정 전입니다. 흐름·엑셀에 경고가 붙습니다."]
    return "\n".join(lines)


@mcp.tool()
def design_map() -> str:
    """트리 **이음 지도** — 어느 트리가 어디로 이어지고, **무엇이 아직 없는지** 낸다.

    구조계산서는 트리 하나로 끝나지 않는다. 휨부재가 "약축"으로 판정되면 약축 트리로,
    인장부재가 블록전단 검토로 가면 연결 기준으로 이어져야 한다.
    이 도구는 그 연결을 한눈에 보여 주고 **끊긴 곳을 목록으로 낸다.**
    그 목록이 곧 "완성하려면 뭘 더 만들어야 하는가" 다.
    """
    g = flows.graph()
    if not g["nodes"]:
        return _NO_TREE
    lines = [f"## 결정트리 지도 — 트리 {len(g['nodes'])}개 · 이음 {len(g['edges'])}개", ""]
    lines += ["| 트리 | 설계법 | 검증 |", "| --- | --- | --- |"]
    for n in sorted(g["nodes"], key=lambda x: x["label"]):
        mark = "✅" if n["확정"] else "⚠️"
        lines.append(f"| {n['label']}{' *(동봉 예제)*' if n['동봉'] else ''} | {n['설계법']} | {mark} {n['검증']} |")

    if g["edges"]:
        lines += ["", "### 이음", "", "| 출발 | 조건 | 도착 | |", "| --- | --- | --- | --- |"]
        for e in sorted(g["edges"], key=lambda x: (x["from"], x["to"])):
            lines.append(f"| {e['from']} | {e['조건'] or '—'} | {e['to']} "
                         f"({e['kind']}) | {'✅' if e['있음'] else '⛔ 없음'} |")

    if g["gaps"]:
        lines += ["", f"### ⛔ 아직 없는 트리 {len(g['gaps'])}개 — 여기서 흐름이 끊깁니다", ""]
        for gap in g["gaps"]:
            lines.append(f"- **{gap['to']}**  ← `{gap['from']}` 단계 `{gap['단계']}`"
                         + (f" (조건: {gap['조건']})" if gap["조건"] else ""))
        lines += ["", "→ `design_template(부재, 단면, 설계법)` 으로 뼈대를 받아 채우고 "
                  "`design_validate` 로 검사하세요."]
    else:
        lines += ["", "### ✅ 끊긴 이음이 없습니다"]
    lines += ["", "> ⚠️ 이음이 이어져 있다는 것과 **흐름이 맞다는 것은 다릅니다.** "
              "각 트리의 `검증` 상태를 함께 보세요."]
    return truncate("\n".join(lines), config.max_chars(), "")


@mcp.tool()
def design_flow(member: str, shape: str = "", method: str = "", domain: str = "",
                with_source: bool = True, excerpt_chars: int = 700) -> str:
    """부재의 **설계 흐름**을 단계별로 낸다. 각 단계의 **근거 조항 원문을 함께 조회해 붙인다.**

    member: 부재 (예: `압축부재`)
    shape: 단면 (예: `원형강관`)
    method: **설계법** (예: `한계상태설계법` · `허용응력설계법`).
            ★같은 부재·같은 단면이라도 설계법이 다르면 근거 기준 자체가 다릅니다.
            트리가 여럿이면 **되묻고, 임의로 고르지 않습니다.**
    with_source: 각 단계의 근거 조항 원문을 함께 낼지. 끄면 흐름만 낸다.
    excerpt_chars: 단계마다 붙일 원문 길이.

    ※ 이 도구는 흐름과 근거까지입니다. **값 입력·계산·최종판단은 설계자가 합니다.**
    """
    t, err, banner = _pick(member, shape, method, domain)
    if err:
        return err
    try:
        text = flows.flow_answer(t, with_source=with_source, excerpt_chars=max(0, excerpt_chars))
    except client.KcscError as e:
        return _err(e)
    if banner:
        text = banner + "\n\n" + text
    return truncate(text, config.max_chars(), "`with_source=false` 로 흐름만 보거나 `excerpt_chars` 를 줄이세요.")


@mcp.tool()
def design_sheet(member: str, shape: str = "", method: str = "", domain: str = "",
                 out_path: str = "") -> str:
    """**빈 단면검토 엑셀**을 만들고 파일 경로를 낸다.

    member·shape·method: `design_flow` 와 같다. 설계법이 애매하면 되묻는다.
    out_path: 저장 위치. 비우면 `~/.kcsc-mcp/sheets/` 에 만든다.

    ★만드는 것은 **빈 템플릿**입니다 — 입력 셀·검토 단계·근거 조항·"식이 있는 원문 위치"까지.
      **계산식과 값은 넣지 않습니다.** 수식은 원문이 이미지이고, 넣는 순간 이 도구가
      구조계산을 대행하는 것이 됩니다. 값·계산·판정은 설계자가 합니다.
    """
    t, err, banner = _pick(member, shape, method, domain)
    if err:
        return err
    path = out_path.strip() or str(sheet.default_dir() /
                                   (sheet.safe_name(str(t.get("부재")), str(t.get("단면"))) + "_단면검토.xlsx"))
    try:
        saved = sheet.blank_excel(t, path)
    except OSError as e:
        return _err(Exception(f"엑셀을 저장하지 못했습니다: {e}"))
    v = str(t.get("검증") or "draft")
    out = [f"빈 단면검토 서식을 만들었습니다.", "", f"**{saved}**", "",
           f"- 트리: {t.get('부재')} / {t.get('단면')} / {t.get('설계법')}",
           f"- 근거: {t.get('근거')}",
           f"- 검증: {v}"]
    w = flows.verify_warning(t) or flows.stamp_line(t)
    if w:
        out += ["", w]
    if banner:
        out += ["", banner]
    out += ["", "> 입력 셀(연노랑)과 산출 셀(연회색)은 **비어 있습니다.** 계산식은 넣지 않았습니다 — "
            "실제 식은 근거 조항 원문에서 확인해 설계자가 넣습니다."]
    return "\n".join(out)


@mcp.tool()
def design_validate(tree_yaml: str = "", path: str = "", check_refs: bool = True) -> str:
    """결정트리를 검사한다. ★**적어 둔 근거 조항이 실재하는지 API 로 확인한다.**

    tree_yaml: 검사할 YAML 본문. (또는)
    path: 검사할 YAML 파일 경로. 둘 다 비우면 사용자 폴더의 트리를 전부 검사한다.
    check_refs: 근거 조항 실재 확인 여부. 끄면 스키마만 본다(빠르다).

    잡아내는 것:
      · 스키마 누락 · 단계 식별자 중복 · **분기가 없는 단계를 가리키는 것**(트리가 끊긴다)
      · **지어낸/오타난/폐지된 조항번호** — 기준에 그 절·표가 실제로 있는지 확인
      · **설계법과 근거 기준의 불일치** (LRFD 트리가 허용응력설계법 기준을 근거로 삼는 등)

    트리는 사람이 쓰고, 근거가 실재하는지는 기계가 검사합니다. 지어낸 조항번호가
    그대로 남는 것이 제일 위험하기 때문입니다.
    """
    targets: list[tuple[str, dict]] = []
    if tree_yaml.strip():
        import yaml as _yaml
        try:
            d = _yaml.safe_load(tree_yaml)
        except _yaml.YAMLError as e:
            return _err(Exception(f"YAML 을 읽지 못했습니다: {e}"))
        if not isinstance(d, dict):
            return _err(Exception("YAML 최상위가 사전이 아닙니다. 트리 한 장(부재 1개)을 넣어 주세요."))
        targets.append(("(입력한 YAML)", d))
    elif path.strip():
        p = Path(path.strip()).expanduser()
        t = flows._load_one(p)
        if not t:
            return _err(Exception(f"트리를 읽지 못했습니다: {p}"))
        targets.append((str(p), t))
    else:
        for t in flows.load_all():
            targets.append((("동봉 예제" if t.get("_동봉") else t.get("_출처", "")), t))
        if not targets:
            return _NO_TREE

    blocks = []
    for label, t in targets:
        try:
            res = flows.validate(t, check_refs=check_refs)
        except client.KcscError as e:
            blocks.append(f"### {label}\n{_err(e)}")
            continue
        head = f"### {t.get('부재','?')} / {t.get('단면','?')} / {t.get('설계법','?')}\n*{label}*"
        body = []
        if res["errors"]:
            body.append(f"**❌ 문제 {len(res['errors'])}건**")
            body += [f"- {e}" for e in res["errors"]]
        else:
            body.append("**✅ 문제 없음**")
        if res["warnings"]:
            body.append("")
            body += [f"- ⚠️ {w}" for w in res["warnings"]]
        if check_refs and res["refs"]:
            body.append("")
            body.append("| 단계 | 근거 | 실재 |")
            body.append("| --- | --- | --- |")
            for r in res["refs"]:
                mark = "✅" if r.get("성립") else "❌"
                body.append(f"| {r['단계']} | {r['원문']} | {mark} {r.get('설명','')} |")
        blocks.append(head + "\n\n" + "\n".join(body))

    return truncate("\n\n---\n\n".join(blocks), config.max_chars(), "`path` 로 하나씩 검사하세요.")


@mcp.tool()
def design_stamp(path: str = "", date: str = "", all_confirmed: bool = False) -> str:
    """확정 트리에 **확정 시점 기록**(`검증일`·`검증기준`)을 박는다.

    path: 트리 파일 경로. (또는)
    all_confirmed: True 면 사용자 폴더의 `검증: 설계자확정` 트리 전부에 박는다(기록 없는 것만).
    date: 확정한 날(YYYY-MM-DD). 비우면 오늘.

    `검증기준` 은 트리가 인용하는 기준마다 **지금 API 가 주는 판**({코드: 버전})이다.
    이후 `design_validate` 가 지금 판과 대조해 **기준이 개정되면 "구판으로 확정된 트리"**
    라고 잡아낸다. 확정 자체가 자동으로 유효하지 않게 되는 것을 놓치지 않기 위한 기록이다.

    ★기록을 박는 것은 기계지만, **개정된 기준을 다시 확인하는 것은 설계자**다.
    """
    import datetime as _dt
    d = date.strip() or _dt.date.today().isoformat()
    targets: list[Path] = []
    if path.strip():
        targets.append(Path(path.strip()).expanduser())
    elif all_confirmed:
        for t in flows.load_all():
            if t.get("_동봉") or not flows.is_confirmed(t):
                continue
            if t.get(flows.VERIFY_DATE_KEY) and t.get(flows.VERIFY_BASIS_KEY):
                continue  # 이미 기록 있음 — 덮어쓰지 않는다
            targets.append(Path(t["_출처"]))
        if not targets:
            return "기록할 트리가 없습니다 — 확정 트리 전부에 이미 `검증일`·`검증기준` 이 있거나, 확정 트리가 없습니다."
    else:
        return _err(Exception("`path` 또는 `all_confirmed=True` 를 주세요."))

    lines = [f"## 확정 시점 기록 — {len(targets)}개 · 검증일 {d}", ""]
    for p in targets:
        try:
            r = flows.stamp_verification(p, d)
        except (ValueError, OSError, client.KcscError) as e:
            lines.append(f"- ❌ `{p.name}` — {e}")
            continue
        basis = " · ".join(f"{c} {v}" for c, v in r["검증기준"].items()) or "(인용 기준 없음)"
        lines.append(f"- ✅ `{p.name}` — {basis}")
    lines += ["", "> 이후 `design_validate` 가 기준 개정을 대조합니다. "
                  "개정이 잡히면 **새 판으로 다시 확인**하고 `design_stamp(path=...)` 로 갱신하세요."]
    return "\n".join(lines)


@mcp.tool()
def design_template(member: str, shape: str, method: str = "") -> str:
    """새 부재용 결정트리 **YAML 뼈대**를 낸다. 빈 폴더에서 형식을 몰라 못 시작하는 것을 막는다.

    member: 부재 (예: `휨부재`)
    shape: 단면 (예: `H형강`)
    method: 설계법. 비우면 한계상태설계법(LRFD)으로 채운다 — ★쓰기 전에 반드시 확인하세요.

    낸 뼈대를 채워 `~/.kcsc-mcp/flows/` 에 넣고 `design_validate` 로 검사하세요.
    **근거로 적은 조항이 실재하는지 그때 기계가 확인합니다.**
    """
    m = method.strip() or "한계상태설계법 (하중저항계수설계법, LRFD)"
    body = flows.template(member, shape, m)
    return (f"```yaml\n{body}```\n\n"
            f"1. 위 뼈대를 채웁니다. **근거는 실재하는 절·표**로 적습니다.\n"
            f"2. `{config.flows_dir() / (sheet.safe_name(member, shape) + '.yaml')}` 로 저장합니다.\n"
            f"3. `design_validate(path=...)` 로 검사합니다 — 근거 조항이 실재하는지 확인합니다.\n"
            f"4. 설계자가 확인하면 `검증: {flows.VERIFY_OK}` 으로 올립니다. 그 전엔 경고가 붙습니다.\n\n"
            f"> ★설계법을 확인하세요. 지금 `{m}` 로 채워 두었습니다. "
            f"설계법이 다르면 근거 기준 자체가 다릅니다.")

# -*- coding: utf-8 -*-
"""설계 결정트리 — 읽기·검사·흐름 렌더.

트리 하나 = 부재 하나 = YAML 한 장. 스키마는 `design_template` 이 내주는 뼈대를 그대로 쓴다.

★이 층이 하는 일은 두 가지다.
  ① **흐름에 근거 원문을 붙인다.** 흐름만 있으면 "그래서 그 값이 뭔데"로 다시 찾아야 하고,
     원문만 있으면 "어느 순서로 보는데"를 모른다. 둘을 붙이는 게 이 도구다.
  ② **적어 둔 조항번호가 실재하는지 API 로 확인한다.** 지어낸 조항번호가 그대로 남는 게
     제일 위험하다. 트리는 사람이 쓰고, 근거가 실재하는지는 기계가 검사한다.

★안전선: 트리는 흐름·근거·빈 템플릿까지만 담는다. 수식(λr·Fcr)은 원문이 이미지라
  값을 박지 않고 "어느 절·표의 식"만 가리킨다. 값·계산·최종판단은 설계자의 몫이다.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from . import client, config, doc as docmod
from .render import html_to_markdown

#: 이 상태여야 경고 없이 실사용한다.
VERIFY_OK = "설계자확정"

#: 기계 검사를 통과했다고 스스로 적은 상태. 검사에 걸리면 그 주장 자체가 오류다.
#
# ★`기계검사통과` 는 draft 와 설계자확정 사이의 중간 상태다.
#   "기계가 잡을 수 있는 것(조항 실재·도달성·이음·설계법·한계상태 누락)은 다 통과했다"는 뜻이고,
#   **설계자가 봤다는 뜻이 아니다.** 그래서 흐름·엑셀의 경고는 여전히 붙되 문구가 다르다.
#   이 구분이 있어야 설계자가 "번호 맞나"가 아니라 "흐름이 실무와 맞나"만 보면 된다.
VERIFY_MACHINE = "기계검사통과"

#: 옛 이름을 여기 넣으면 그 이름으로 확정된 트리도 확정으로 본다.
#
#   지금은 비어 있다. 트리 스키마의 확정 표시를 바꿀 일이 생기면, 옛 이름을 여기 넣어
#   **한 판 동안 둘 다 받아 주고** 트리를 다 옮긴 뒤 비우는 식으로 쓴다.
#   비운 채로 이름만 바꾸면 그 이름으로 확정된 트리가 전부 '미확정' 으로 떨어지고
#   흐름·엑셀에 경고가 붙는다.
VERIFY_OK_ALIASES: tuple[str, ...] = ()

#: `검증:` 에 쓸 수 있는 값.
VERIFY_STATES_CANON = (VERIFY_OK, VERIFY_MACHINE, "draft", "예제", "수정필요")

#: 실제로 받아 주는 값 — 별칭이 있으면 함께 받는다.
VERIFY_STATES = VERIFY_STATES_CANON + VERIFY_OK_ALIASES


def safe_name(*parts: str) -> str:
    """부재·단면 이름 → 파일 이름으로 쓸 수 있는 문자열.

    (엑셀 도구를 빼면서 sheet.py 에서 옮겨 왔다 — 2026-08-25. 트리 yaml 이름을
     안내할 때 `design_template` 이 쓴다.)
    """
    s = "_".join(p for p in parts if p)
    s = re.sub(r"[^\w가-힣().-]+", "_", s).strip("_")
    return (s or "설계흐름")[:80]


def verify_state(t: dict) -> str:
    """트리의 검증 상태. **별칭은 정식 값으로 접어서** 돌려준다."""
    v = str(t.get("검증") or "draft").strip()
    return VERIFY_OK if v in VERIFY_OK_ALIASES else v


def is_confirmed(t: dict) -> bool:
    """설계자가 확정한 트리인가."""
    return verify_state(t) == VERIFY_OK

REQUIRED_TOP = ("부재", "단면", "설계법", "근거", "목표검토", "흐름", "검증")

#: ★확정 시점 기록. `검증일`(확정한 날) · `검증기준`(그때 각 기준의 판 — {코드: 버전}).
#
#   왜 있나 — 트리는 "그날의 기준" 을 보고 확정한 것이다. 기준이 개정되면 확정은 자동으로
#   유효하지 않다. `검증기준` 이 있으면 검사가 **지금 판과 확정 당시 판을 대조**해
#   "구판으로 확정된 트리" 를 잡아낸다. 없으면 잡을 방법이 없다.
#   (검토를 한 바퀴 돌려 트리가 확정된 뒤에 넣은 장치다.)
VERIFY_DATE_KEY = "검증일"
VERIFY_BASIS_KEY = "검증기준"


# ── 트리 읽기 ────────────────────────────────────────────────────────────────
def bundled_dir() -> Path:
    return Path(__file__).parent / "flows"


#: 파싱 캐시 — {경로: (mtime_ns, size, 트리)}.
#: ★`resolve_link`·`find` 가 링크마다 `load_all()` 을 다시 불러, 트리 35개·이음 110개면
#:   `design_map` 한 번에 YAML 을 4천 번 넘게 파싱했다(실측 22초). 파일이 바뀌면(mtime·크기)
#:   다시 읽으므로 사용자가 YAML 을 고쳐도 낡은 트리를 내주지 않는다. 읽은 트리는 아무도
#:   고쳐 쓰지 않는다(전수 확인) — 같은 객체를 돌려줘도 안전하다.
_PARSE_CACHE: dict[str, tuple[int, int, dict]] = {}


def _load_one(path: Path) -> dict | None:
    try:
        st = path.stat()
    except OSError:
        return None
    key = str(path)
    hit = _PARSE_CACHE.get(key)
    if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
        return hit[2]
    try:
        d = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        _PARSE_CACHE.pop(key, None)
        return None
    if not isinstance(d, dict):
        _PARSE_CACHE.pop(key, None)
        return None
    d["_출처"] = str(path)
    d["_동봉"] = bundled_dir() in path.parents
    _PARSE_CACHE[key] = (st.st_mtime_ns, st.st_size, d)
    return d


def load_all() -> list[dict]:
    """쓸 수 있는 트리 전부. **사용자 폴더가 동봉 예제를 덮어쓴다.**

    같은 (부재·단면·설계법) 이면 사용자 것이 이긴다 — 각 회사의 트리가 그 회사 기준이다.
    """
    trees: dict[tuple, dict] = {}
    for d in (bundled_dir(), config.flows_dir()):
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.yaml")) + sorted(d.glob("*.yml")):
            t = _load_one(p)
            if t:
                trees[key_of(t)] = t
    return list(trees.values())


def key_of(t: dict) -> tuple[str, str, str]:
    return (_norm(t.get("부재")), _norm(t.get("단면")), _norm(t.get("설계법")))


def _norm(s: Any) -> str:
    return re.sub(r"\s+", "", str(s or "")).lower()


# ── 트리끼리 잇기 ────────────────────────────────────────────────────────────
#
# ★한 트리가 자기 범위 끝에서 그냥 끊기면 구조계산서가 안 된다.
#   설계자가 "약축 휨"으로 판정되면 거기서 멈추는 게 아니라 **약축 트리로 이어져야** 한다.
#   그래서 분기와 단계에 두 가지 이음을 둔다.
#
#     다음트리: "휨부재 / 약축 H형강"          ← 다른 결정트리로
#     다음기준: "KDS 14 31 25  4.1.4.3"        ← 다른 기준의 조항으로
#
#   `다음트리` 에 설계법을 안 쓰면 **현재 트리의 설계법을 물려받는다.**
#   설계법이 다르면 다른 트리라는 원칙이 이음에서도 깨지지 않아야 하기 때문이다.
#
#   ★단면을 가리지 않는 트리(예: 조합력부재는 H형강·원형강관·각형강관을 다 다룬다)로
#     이을 때는 단면을 적지 않는다. 설계법까지 적어야 하면 **자리를 비워 둔다** —
#     "부재 / / 설계법". 자리를 지키지 않으면 설계법을 단면으로 읽는다.
#
#   가리킨 트리가 아직 없으면 **"아직 없다"고 드러낸다.** 조용히 끊지 않는다 —
#   그 목록이 곧 "구조계산서를 완성하려면 뭘 더 만들어야 하는가"다.

def parse_link(text: str, default_method: str = "") -> dict:
    """`"휨부재 / 약축 H형강"` → {부재, 단면, 설계법}. 설계법 생략 시 물려받는다."""
    parts = [p.strip() for p in str(text or "").split("/")]
    parts += [""] * (3 - len(parts))
    return {"부재": parts[0], "단면": parts[1], "설계법": parts[2] or default_method}


def resolve_link(link: dict) -> dict | None:
    hits = find(link["부재"], link["단면"], link["설계법"])
    return hits[0] if len(hits) == 1 else (hits[0] if hits else None)


def link_label(link: dict) -> str:
    return " / ".join(x for x in (link["부재"], link["단면"], link["설계법"]) if x)


def links_of(t: dict) -> list[dict]:
    """트리가 내보내는 이음 전부 → [{단계, 조건, kind, ref}]"""
    out = []
    for s in t.get("흐름", []) or []:
        if not isinstance(s, dict):
            continue
        sid = s.get("단계")
        # 위임근거는 단계에 붙지만 그 단계의 분기가 내보내는 이음에도 그대로 적용된다.
        deleg = s.get("위임근거")
        for holder, cond in [(s, "")] + [(b, str(b.get("조건") or "")) for b in (s.get("분기") or [])
                                         if isinstance(b, dict)]:
            base = {"단계": sid, "조건": cond, "위임근거": holder.get("위임근거") or deleg}
            if holder.get("다음트리"):
                out.append({**base, "kind": "트리", "ref": str(holder["다음트리"])})
            if holder.get("다음기준"):
                out.append({**base, "kind": "기준", "ref": str(holder["다음기준"])})
    return out


# ── 확정 시점의 기준 판 ───────────────────────────────────────────────────────
def cited_codes(t: dict) -> list[str]:
    """트리가 근거·위임근거·다음기준으로 인용하는 기준 코드(정규화)의 중복 없는 목록."""
    seen: list[str] = []

    def _add(text):
        c = parse_ref(str(text or "")).get("code")   # 이미 정규화된 꼴 — 예: 'KDS 143110'
        if c and c not in seen:
            seen.append(c)

    _add(t.get("근거"))
    _add(t.get("위임근거"))
    for s in t.get("흐름", []) or []:
        if not isinstance(s, dict):
            continue
        _add(s.get("근거"))
        _add(s.get("위임근거"))
    for lk in links_of(t):
        if lk["kind"] == "기준":
            _add(lk["ref"])
    return seen


def current_basis(t: dict) -> dict[str, str]:
    """트리가 인용하는 기준마다 **지금 API 가 주는 판** → {코드: 버전}.

    `검증기준` 에 적을 값이자, 검사 때 대조 기준이다. 조회 실패한 코드는 뺀다.
    """
    out: dict[str, str] = {}
    for c in cited_codes(t):
        try:
            entry, _ = client.resolve(c)
            v = str(entry.get("version") or "").strip()
            if v:
                out[c] = v
        except client.KcscError:
            continue
    return out


def basis_drift(t: dict) -> tuple[list[str], list[str]]:
    """확정 당시 판(`검증기준`)과 지금 판을 대조 → (개정된 것, 기록에 없는 것).

    개정된 것 = "구판으로 확정된 트리" 신호. 기록에 없는 것 = 확정 후 인용이 늘었거나
    기록이 낡았다는 신호. 둘 다 사람이 봐야 할 목록이다.
    """
    recorded = t.get(VERIFY_BASIS_KEY) or {}
    if not isinstance(recorded, dict):
        return [], []
    now = current_basis(t)
    changed, missing = [], []
    for c, v_now in now.items():
        v_then = str(recorded.get(c) or "").strip()
        if not v_then:
            missing.append(f"{c} (지금 {v_now})")
        elif v_then != v_now:
            changed.append(f"{c}: 확정 당시 {v_then} → 지금 {v_now}")
    return changed, missing


_STAMP_LINE_RE = re.compile(r"^(검증일|검증기준):.*(?:\n[ \t]+.*)*\n?", re.M)


def stamp_verification(path: Path, date: str, basis: dict[str, str] | None = None) -> dict:
    """트리 파일에 `검증일`·`검증기준` 을 **글자 그대로** 박는다 (yaml.dump 를 쓰지 않는다 —
    주석·작성 이력을 살려야 하니 `검증:` 줄 바로 앞에 끼워 넣는다).

    basis 를 비우면 지금 API 판을 조회해 넣는다. → {"검증일", "검증기준", "path"}
    """
    text = path.read_text(encoding="utf-8")
    t = yaml.safe_load(text)
    if not isinstance(t, dict):
        raise ValueError(f"트리를 읽지 못했습니다: {path}")
    if basis is None:
        basis = current_basis(t)
    # 기존 기록 제거
    text = _STAMP_LINE_RE.sub("", text)
    block = f"{VERIFY_DATE_KEY}: \"{date}\"\n{VERIFY_BASIS_KEY}:\n"
    for c, v in basis.items():
        block += f"  \"{c}\": \"{v}\"\n"
    # `검증:` 마지막 줄 앞에 삽입
    m = re.search(r"^검증:.*$", text, re.M)
    if not m:
        raise ValueError(f"`검증:` 줄이 없습니다: {path}")
    text = text[:m.start()] + block + text[m.start():]
    path.write_text(text, encoding="utf-8")
    return {"검증일": date, "검증기준": basis, "path": str(path)}


def resolve_domain(domain: str = "") -> str:
    """분야를 정한다. **말이 없으면 교량**(config.default_domain)."""
    d = _norm(domain)
    if not d:
        return config.default_domain()
    # 옛 이름(건축)으로 물어도 받아 준다 — 이미 그렇게 쓰던 곳이 있다.
    for 옛, 지금 in config.DOMAIN_ALIASES.items():
        if 옛 in d:
            return 지금
    if "강구조" in d:
        return "강구조"
    if "교량" in d or "교" == d:
        return "교량"
    return domain.strip()


def method_for_domain(domain: str) -> str:
    """그 분야의 기본 설계법."""
    return config.DOMAIN_METHODS.get(resolve_domain(domain), "")


def domain_of_tree(t: dict) -> str:
    """트리가 어느 분야인가 — 주 근거 기준의 코드로 판별한다."""
    ref = parse_ref(t.get("근거", ""))
    if not ref["code"]:
        return "기타"
    return config.domain_of_code(ref["code"].split()[-1])


def find(member: str, shape: str = "", method: str = "") -> list[dict]:
    """부재(필수)·단면·설계법으로 트리를 고른다. 부분일치를 받는다."""
    m, s, me = _norm(member), _norm(shape), _norm(method)
    out = []
    for t in load_all():
        k = key_of(t)
        if m and m not in k[0]:
            continue
        if s and s not in k[1]:
            continue
        if me and me not in k[2]:
            continue
        out.append(t)
    return out


# ── 근거 참조 파싱 ───────────────────────────────────────────────────────────
#: `KDS 14 31 10` · `KDS 143110` · `LHCS 31 20 15 10` 을 받는다.
#: ★두 자리씩 끊어 읽는 것이 중요하다. 숫자를 뭉뚱그려 받으면 바로 뒤의 조항번호
#:   `4.2.1.1.3` 의 `4` 까지 먹어 `1431104` 가 된다 (실제로 그랬다).
#:   뒤에 숫자나 점이 붙으면 코드가 아니라고 본다.
_CODE_RE = re.compile(
    r"\b(" + "|".join(config.CODE_TYPES) + r")\s*(\d{6}(?:\d{2})?|\d{2}(?:\s+\d{2}){2,3})(?![\d.])", re.I)
_CAPTION_RE = re.compile(r"(표|그림|식)\s*(\d+(?:\.\d+)*[-–]\d+)")
_CLAUSE_RE = re.compile(r"(?<![\d.\-])(\d+(?:\.\d+)+)(?![\d\-])")


def parse_ref(text: str) -> dict:
    """`근거:` 한 줄 → {code, clause, captions}.

    예) "KDS 14 31 10  4.2.1.1.3 + 표 4.2-2 (9행: 원형강관)"
        → code='KDS 143110' · clause='4.2.1.1.3' · captions=['표 4.2-2']

    `표 4.2-2` 의 `4.2` 를 조항번호로 착각하지 않도록 캡션을 먼저 걷어내고 찾는다.
    """
    s = str(text or "")
    code = ""
    m = _CODE_RE.search(s)
    if m:
        digits = re.sub(r"\D", "", m.group(2))
        code = f"{m.group(1).upper()} {digits}"
        s = s[:m.start()] + " " + s[m.end():]
    captions = [f"{a} {b}" for a, b in _CAPTION_RE.findall(s)]
    s = _CAPTION_RE.sub(" ", s)
    cm = _CLAUSE_RE.search(s)
    return {"code": code, "clause": cm.group(1) if cm else "", "captions": captions}


def check_ref(ref: dict) -> tuple[bool, str]:
    """근거가 **실재하는지** API 로 확인한다. → (성립여부, 설명)

    오타·폐지된 조항·개정으로 번호가 바뀐 조항을 여기서 잡는다.
    """
    if not ref["code"]:
        return False, "기준 코드가 없습니다 (예: `KDS 14 31 10`)"
    try:
        d, _ = client.document(ref["code"])
    except client.KcscError as e:
        return False, str(e).splitlines()[0]

    if ref["clause"]:
        if not docmod.section_items(d, ref["clause"]):
            return False, f"`{ref['clause']}` 절이 그 기준에 없습니다"
    missing = [c for c in ref["captions"] if not _has_caption(d, c)]
    if missing:
        return False, f"{', '.join(missing)} 를 그 기준에서 찾지 못했습니다"
    where = ref["clause"] or "문서"
    return True, f"{ref['code']} {where}" + (f" · {' · '.join(ref['captions'])}" if ref["captions"] else "")


def _has_caption(d: dict, caption: str) -> bool:
    want = _norm(caption)
    return any(_norm(it.get("label")) == want for it in docmod.items(d))


# ── 라우팅 표 대조 ───────────────────────────────────────────────────────────
#
# ★기준이 이미 "조건 → 적용 절 → 한계상태" 를 표로 갖고 있다.
#     표 4.3-1 휨부재 단면의 분류   | 해당 절 | 단면 형태 | 플랜지 | 웨브 | 한계상태 |
#     표 4.2-1 압축부재 적용 절     | 단면 | 절 | 한계상태 | 절 | 한계상태 |
#   단면 형태만 그림이고 **절 번호와 한계상태는 텍스트**다. 그래서 기계가 읽어
#   "이 절을 근거로 삼았는데 표가 드는 한계상태 중 하나가 트리에 없다" 를 잡을 수 있다.
#   누락은 사람 눈이 제일 놓치기 쉬운 종류다.

#: 앞글자를 **선택**으로 둔다. `+` 로 두면 두 글자짜리 `항복` 을 통째로 놓친다(실제로 놓쳤다).
_LIMIT_RE = re.compile(r"[가-힣]*(?:좌굴|항복|파단|파괴)")
_CLAUSE_IN_CELL_RE = re.compile(r"\d+(?:\.\d+){2,}")
_routing_cache: dict[str, dict] = {}


def _table_rows(md: str) -> list[list[str]]:
    rows = []
    for line in md.split("\n"):
        line = line.strip()
        if line.startswith("|") and set(line) - set("|- "):
            rows.append([c.strip() for c in line.strip("|").split("|")])
    return rows


def routing_table(code: str) -> dict[str, set[str]]:
    """기준의 라우팅 표에서 {조항번호: {한계상태…}} 를 뽑는다.

    한 행 안에서 **조항번호가 든 칸 다음에 오는, 한계상태 낱말이 든 칸**을 짝짓는다.
    표마다 열 구성이 달라(병합 셀 때문에 열 번호가 어긋난다) 열 위치로 맞추면 깨진다.
    """
    if code in _routing_cache:
        return _routing_cache[code]
    out: dict[str, set[str]] = {}
    try:
        d, _ = client.document(code)
    except client.KcscError:
        _routing_cache[code] = out
        return out
    for it in docmod.items(d):
        html = it.get("contents") or ""
        if "한계상태" not in html or "<table" not in html.lower():
            continue
        for cells in _table_rows(html_to_markdown(html)):
            pending: list[str] = []
            for cell in cells:
                clauses = _CLAUSE_IN_CELL_RE.findall(cell)
                if clauses:
                    pending = clauses
                    continue
                states = set(_LIMIT_RE.findall(cell))
                if states and pending:
                    for c in pending:
                        out.setdefault(c, set()).update(states)
                    pending = []
    _routing_cache[code] = out
    return out


def missing_limit_states(t: dict, refs: list[dict]) -> list[str]:
    """트리가 든 조항에 대해, 라우팅 표가 드는 한계상태 중 트리에 안 보이는 것."""
    blob = _norm(yaml.safe_dump(t, allow_unicode=True))
    out = []
    for r in refs:
        if not r.get("code") or not r.get("clause"):
            continue
        states = routing_table(r["code"]).get(r["clause"])
        if not states:
            continue
        gone = sorted(s for s in states if _norm(s) not in blob)
        if gone:
            out.append(f"`{r['code']} {r['clause']}` 의 한계상태 중 **{', '.join(gone)}** 이(가) "
                       "트리에 보이지 않습니다 (표에 적힌 것)")
    return out


def excerpt(ref: dict, limit: int = 700) -> str:
    """근거 조항의 **원문 일부**. 흐름 옆에 붙여 "그 값이 뭔데"를 없앤다."""
    if not ref["code"] or not ref["clause"]:
        return ""
    try:
        d, _ = client.document(ref["code"])
    except client.KcscError:
        return ""
    its = docmod.section_items(d, ref["clause"])
    if not its:
        return ""
    body, _ = docmod.render_items(its)
    body = body.strip()
    if len(body) > limit:
        body = body[:limit].rsplit("\n", 1)[0] + f"\n…(이하 생략 — `kcsc_read('{ref['code']}', '{ref['clause']}')` 로 전체)"
    return body


# ── 설계법 대조 ──────────────────────────────────────────────────────────────
#: 설계법 이름 → 그 설계법 기준의 **이름에 들어가는 말**.
#
# ★2026-08-06 정정: 세 가지가 **서로 다른 기준 계열**이다. 뭉뚱그리면 안 된다.
#     허용응력설계법(ASD)      KDS 14 30 xx  강구조 부재 설계기준(허용응력설계법)
#     하중저항계수설계법(LRFD)  KDS 14 31 xx  강구조 부재 설계기준(하중저항계수설계법)
#     한계상태설계법            KDS 24 14 31  강교 설계기준(한계상태설계법)  ← 교량 계열
#   처음에 "한계상태 = 하중저항계수" 로 묶어 뒀는데, 그러면 교량 한계상태설계법을 찾는
#   사람에게 건축 LRFD 트리를 내주게 된다. 설계법이 다르면 기준이 다르다는 원칙 그 자체를
#   깨는 버그였다.
_METHODS = (("허용응력", "허용응력"), ("asd", "허용응력"),
            ("하중저항계수", "하중저항계수"), ("lrfd", "하중저항계수"),
            ("한계상태", "한계상태"))


def method_conflict(method: str, standard_name: str) -> str:
    """설계법과 근거 기준이 어긋나는가. 어긋나면 왜인지 돌려준다.

    ★같은 부재·같은 단면이라도 설계법이 갈리면 **근거 기준 자체가 다르다.**
      KDS 14 31 10 은 하중저항계수설계법, KDS 14 30 10 은 허용응력설계법이다.
      기준 이름에 그 말이 그대로 들어 있으므로 추측이 아니라 대조로 잡는다.
    """
    m, name = _norm(method), str(standard_name or "")
    marks = {mark for kw, mark in _METHODS if kw in m}
    if not marks:
        return ""
    others = {mark for _, mark in _METHODS} - marks
    hit = [o for o in others if o in name]
    if hit and not any(mk in name for mk in marks):
        return f"설계법은 `{method}` 인데 근거 기준 「{standard_name}」 은 {hit[0]}설계법입니다"
    return ""


# ── 렌더 ─────────────────────────────────────────────────────────────────────
def verify_warning(t: dict) -> str:
    v = verify_state(t)          # 옛 이름(별칭)도 확정으로 접어서 본다
    if v == VERIFY_OK:
        return ""
    if v == VERIFY_MACHINE:
        return ("> ⚠️ 이 트리는 **기계 검사만 통과**한 상태입니다 (`검증: 기계검사통과`).\n"
                "> 조항·식 번호 실재, 분기 도달성, 트리 이음, 설계법 대조, 한계상태 누락까지는\n"
                "> 확인됐습니다. **하지만 흐름이 실무와 맞는지는 아직 아무도 보지 않았습니다.**\n"
                f"> 설계자가 확인해 `{VERIFY_OK}` 으로 올려야 실사용할 수 있습니다.")
    if v == "예제":
        return ("> ⚠️ 이 트리는 **패키지 동봉 예제**입니다 (`검증: 예제`). 형식을 보여 주려고 넣은 것이라\n"
                "> **그대로 설계에 쓰면 안 됩니다.** 자기 트리를 `~/.kcsc-mcp/flows/` 에 넣어 쓰세요.")
    return f"> ⚠️ 이 트리는 검증상태 **`{v}`** — 설계자 확정 전 미리보기입니다."


def stamp_line(t: dict) -> str:
    """확정 시점 기록 한 줄. 확정 트리가 **언제·어느 판을 보고** 확정됐는지 답변에 드러낸다.

    기준이 개정됐다면 그 사실을 여기서도 말한다 — 흐름을 보는 사람이 제일 먼저 알아야 한다.
    """
    if not is_confirmed(t):
        return ""
    d = t.get(VERIFY_DATE_KEY)
    b = t.get(VERIFY_BASIS_KEY)
    if not d or not isinstance(b, dict) or not b:
        return ("> ⚠️ 확정 트리인데 **확정 시점 기록(`검증일`·`검증기준`)이 없습니다** — "
                "기준이 개정돼도 알 수 없습니다. `design_stamp` 로 기록하세요.")
    basis = " · ".join(f"{c} {v}" for c, v in b.items())
    line = f"> ✅ **확정 {d}** — 확정 당시 기준 판: {basis}"
    try:
        changed, _ = basis_drift(t)
    except Exception:  # 조회 실패는 여기서 삼킨다 — 흐름 답변을 막지 않는다
        changed = []
    if changed:
        line += ("\n> ❌ **확정 이후 기준이 개정되었습니다** — " + " · ".join(changed)
                 + ". **이 흐름은 구판 기준으로 확정된 것**이니 새 판으로 다시 확인하세요.")
    return line


SAFETY_LINE = (
    "> **안전선**: 위는 기준서의 *흐름 안내*입니다. λr·Fcr·φc 등 실제 값·식은 원문이 이미지라\n"
    "> 여기 담기지 않습니다. 반드시 원문에서 확인하고, **값 입력·계산·최종판단은 설계자**가 합니다.\n"
    "> 이 도구는 구조계산을 대행하지 않습니다."
)


def _link_line(holder: dict, t: dict, indent: str) -> str:
    """이음 한 줄. 가리킨 트리가 없으면 **없다고 적는다.**"""
    out = []
    if holder.get("다음트리"):
        link = parse_link(str(holder["다음트리"]), t.get("설계법", ""))
        tgt = resolve_link(link)
        if tgt:
            out.append(f"{indent}➡️ **다음 트리: {link_label(link)}** "
                       f"— `design_flow('{link['부재']}', '{link['단면']}', '{link['설계법']}')`")
        else:
            out.append(f"{indent}⛔ **다음 트리: {link_label(link)} — 아직 없습니다.** "
                       "`design_template` 로 만들어야 여기서 이어집니다")
    if holder.get("다음기준"):
        out.append(f"{indent}➡️ **다음 기준: {holder['다음기준']}** — `kcsc_read` 로 확인")
    return "\n".join(out)


def graph() -> dict:
    """트리 전체의 이음 지도 → {nodes, edges, gaps}. "무엇을 더 만들어야 하는가"를 낸다."""
    trees = load_all()
    nodes, edges, gaps = [], [], []
    for t in trees:
        nodes.append({"label": " / ".join(str(t.get(k) or "") for k in ("부재", "단면")),
                      "설계법": t.get("설계법"), "검증": t.get("검증") or "draft",
                      # ★`검증` 은 화면에 보여 줄 **파일에 적힌 값**, `확정` 은 판정용
                      #   (옛 이름으로 확정된 트리도 True). 둘을 갈라 두어야 표시와 판정이 안 엇갈린다.
                      "확정": is_confirmed(t),
                      "동봉": bool(t.get("_동봉"))})
        for lk in links_of(t):
            src = " / ".join(str(t.get(k) or "") for k in ("부재", "단면"))
            if lk["kind"] == "기준":
                edges.append({"from": src, "to": lk["ref"], "kind": "기준",
                              "조건": lk["조건"], "있음": True})
                continue
            link = parse_link(lk["ref"], t.get("설계법", ""))
            ok = resolve_link(link) is not None
            edges.append({"from": src, "to": link_label(link), "kind": "트리",
                          "조건": lk["조건"], "있음": ok})
            if not ok:
                gaps.append({"from": src, "단계": lk["단계"], "조건": lk["조건"],
                             "to": link_label(link)})
    return {"nodes": nodes, "edges": edges, "gaps": gaps}


def flow_answer(t: dict, with_source: bool = True, excerpt_chars: int = 700) -> str:
    """흐름 답변 — 각 단계의 **근거 조항 원문을 그 자리에서 함께 낸다.**"""
    L = [f"## {t['부재']} 설계 흐름 — {t.get('단면','')} ({t.get('설계법','')})",
         f"**근거 기준**: {t['근거']}  |  **최종 검토식**: {t['목표검토']}"]
    w = verify_warning(t) or stamp_line(t)
    if w:
        L += ["", w]

    L += ["", "### 입력값 (설계자가 넣는 값)"]
    for x in t.get("입력", []) or []:
        L.append(f"- **{x.get('기호')}** = ___  ({x.get('설명')}, {x.get('단위')})")

    L += ["", "### 설계 흐름"]
    for s in t.get("흐름", []) or []:
        L.append(f"**[{s.get('단계')}] {s.get('이름')}**  —  근거 `{s.get('근거','')}`")
        if s.get("작업"):
            L.append(f"  - 작업: {s['작업']}")
        if s.get("식위치"):
            L.append(f"  - 참조식: {s['식위치']}")
        for b in s.get("분기", []) or []:
            tail = f"(→ 단계 {b.get('다음')})" if b.get("다음") else ""
            L.append(f"  - 분기: **{b.get('조건')}** → {b.get('결과')} {tail}".rstrip())
            L += [x for x in [_link_line(b, t, indent="      ")] if x]
        L += [x for x in [_link_line(s, t, indent="  - ")] if x]
        if s.get("출력"):
            L.append(f"  - 산출: {s['출력']}")
        if with_source:
            ref = parse_ref(s.get("근거", ""))
            body = excerpt(ref, excerpt_chars)
            if body:
                L.append("")
                L.append(f"  📖 **근거 원문** — {ref['code']} {ref['clause']}")
                L.append("\n".join(("  > " + ln) if ln else "  >" for ln in body.splitlines()))
        L.append("")

    if t.get("안전주의"):
        L += ["### 주의", str(t["안전주의"]).strip(), ""]
    L += ["---", SAFETY_LINE]
    src = "패키지 동봉 예제" if t.get("_동봉") else t.get("_출처", "")
    L.append(f"\n*트리 출처: {src}*")
    return "\n".join(L)


# ── 검사 ─────────────────────────────────────────────────────────────────────
def validate(t: dict, check_refs: bool = True) -> dict:
    """트리를 검사한다 → {errors, warnings, refs}.

    스키마만 보는 게 아니라 **근거 조항이 실재하는지 API 로 확인**하는 것이 요점이다.
    """
    errors: list[str] = []
    warnings: list[str] = []
    refs: list[dict] = []

    for k in REQUIRED_TOP:
        if not t.get(k):
            errors.append(f"필수 항목 `{k}` 가 없습니다")

    v_raw = str(t.get("검증") or "")
    v = verify_state(t)
    if v and v not in VERIFY_STATES:
        errors.append(f"`검증: {v}` 는 쓸 수 없는 값입니다. 가능: {' · '.join(VERIFY_STATES)}")
    elif v == VERIFY_MACHINE:
        warnings.append(f"`검증: {VERIFY_MACHINE}` — 기계 검사만 통과한 상태입니다. "
                        f"**흐름이 실무와 맞는지는 설계자가 봐야** `{VERIFY_OK}` 가 됩니다")
    elif v != VERIFY_OK:
        warnings.append(f"`검증: {v or '없음'}` — 실사용하려면 설계자가 확인 후 `{VERIFY_OK}` 로 올려야 합니다")

    syms = [x.get("기호") for x in (t.get("입력") or []) if isinstance(x, dict)]
    dup = {s for s in syms if syms.count(s) > 1}
    if dup:
        errors.append(f"입력 기호가 겹칩니다: {', '.join(sorted(dup))}")
    for x in t.get("입력") or []:
        if not isinstance(x, dict) or not x.get("기호"):
            errors.append(f"입력 항목에 `기호` 가 없습니다: {x!r}")

    steps = t.get("흐름") or []
    if not isinstance(steps, list) or not steps:
        errors.append("`흐름` 이 비어 있습니다 — 결정트리가 없는 트리입니다")
        steps = []
    ids = [str(s.get("단계")) for s in steps if isinstance(s, dict)]
    dupi = {i for i in ids if ids.count(i) > 1}
    if dupi:
        errors.append(f"단계 식별자가 겹칩니다: {', '.join(sorted(dupi))}")

    # ★도달할 수 없는 단계를 잡는다.
    #   흐름은 위에서 아래로 흐르되 `분기` 가 있으면 거기서 갈라진다. 그러니 분기로 끝난
    #   단계 **다음**에 오는 단계는 어느 분기의 목적지이기도 해야 한다. 아니면 아무도
    #   그 단계에 닿지 못한다 — 실제로 이 검사를 넣자마자 자체 작성 트리에서 하나 나왔다.
    targets = {str(b.get("다음")) for s in steps if isinstance(s, dict)
               for b in (s.get("분기") or []) if isinstance(b, dict)}
    for prev, cur in zip(steps, steps[1:]):
        if not (isinstance(prev, dict) and isinstance(cur, dict)):
            continue
        if prev.get("분기") and str(cur.get("단계")) not in targets:
            errors.append(
                f"단계 `{cur.get('단계')}` 에 도달할 경로가 없습니다 — "
                f"앞 단계 `{prev.get('단계')}` 가 분기로 끝나는데 어느 분기도 여기를 가리키지 않습니다")

    for s in steps:
        if not isinstance(s, dict):
            errors.append(f"흐름 항목이 사전이 아닙니다: {s!r}")
            continue
        sid = s.get("단계")
        for k in ("단계", "이름", "근거"):
            if not s.get(k):
                errors.append(f"단계 `{sid}` 에 `{k}` 가 없습니다")
        # ★분기가 가리키는 다음 단계가 실재하는가 — 없으면 트리가 거기서 끊긴다
        for b in s.get("분기") or []:
            nxt = str(b.get("다음") or "")
            has_link = bool(b.get("다음트리") or b.get("다음기준"))
            if not nxt:
                if not has_link:
                    errors.append(f"단계 `{sid}` 의 분기에 `다음`·`다음트리`·`다음기준` 이 모두 없습니다 "
                                  "— 여기서 흐름이 끊깁니다")
            elif nxt not in ids:
                errors.append(f"단계 `{sid}` 의 분기가 없는 단계 `{nxt}` 를 가리킵니다")
        if s.get("근거"):
            refs.append({"단계": sid, **parse_ref(s["근거"]), "원문": s["근거"],
                         "위임근거": s.get("위임근거")})
        # ★`위임근거` 는 "다른 설계법 기준을 인용해도 되는 근거" 다.
        #   예) KDS 24 14 31(교량 한계상태) 4.4(1) 이 "인장부재 설계는 KDS 14 31 10(4.1)을
        #   따른다"고 명시한다. 그런 위임이 있으면 설계법 불일치가 아니라 정당한 인용이다.
        #   다만 **위임 조항 자체가 실재해야** 한다 — 아래에서 함께 확인한다.
        if s.get("위임근거"):
            refs.append({"단계": f"{sid}(위임)", **parse_ref(s["위임근거"]),
                         "원문": s["위임근거"]})

    top_ref = parse_ref(t.get("근거", ""))
    if top_ref["code"]:
        refs.insert(0, {"단계": "(주 근거)", **top_ref, "원문": str(t.get("근거"))})

    # ★이음 검사 — 가리킨 트리가 아직 없으면 **없다고 드러낸다**(오류가 아니라 할 일이다)
    gaps: list[dict] = []
    for lk in links_of(t):
        if lk["kind"] == "트리":
            link = parse_link(lk["ref"], t.get("설계법", ""))
            if resolve_link(link) is None:
                gaps.append({**lk, "label": link_label(link)})
                warnings.append(
                    f"단계 `{lk['단계']}` 가 가리키는 트리가 아직 없습니다 — **{link_label(link)}**"
                    + (f" (조건: {lk['조건']})" if lk["조건"] else "")
                    + "  → `design_template` 로 만드세요")
        else:
            refs.append({"단계": f"{lk['단계']}→", **parse_ref(lk["ref"]), "원문": lk["ref"],
                         "위임근거": lk.get("위임근거")})

    delegated: dict[tuple, list[str]] = {}
    if check_refs:
        for r in refs:
            ok, msg = check_ref(r)
            r["성립"] = ok
            r["설명"] = msg
            if not ok:
                errors.append(f"근거 실재 확인 실패 — 단계 `{r['단계']}`: {msg}")
            elif r["code"]:
                try:
                    entry, _ = client.resolve(r["code"])
                    conflict = method_conflict(t.get("설계법", ""), entry.get("name", ""))
                    if conflict and r.get("위임근거"):
                        # 위임 근거가 있으면 정당한 인용이다. 다만 눈에는 보이게 남긴다.
                        # ★같은 위임이 열댓 단계에 반복되므로 **묶어서 한 줄로** 낸다.
                        #   같은 말이 15줄 쌓이면 아무도 안 읽는다.
                        delegated.setdefault((str(r["위임근거"]), entry.get("name", "")),
                                             []).append(str(r["단계"]))
                    elif conflict:
                        errors.append(f"단계 `{r['단계']}`: {conflict}"
                                      " (의도한 인용이면 `위임근거` 에 위임 조항을 적으세요)")
                except client.KcscError:
                    pass

    for (deleg, name), steps in delegated.items():
        warnings.append(f"단계 {', '.join('`%s`' % s for s in steps)} 는 **다른 설계법 기준을 "
                        f"인용**합니다 — 「{name}」. 위임 근거: `{deleg}` "
                        "(정당한 인용이지만 **저항계수 φ 는 이 트리 설계법의 값**을 써야 합니다)")

    # ★확정 시점 기록 대조 — "구판으로 확정된 트리" 를 잡는다.
    #   확정된 트리인데 `검증일`·`검증기준` 이 없으면 대조할 수 없다는 것부터
    #   드러내고, 있으면 지금 판과 대조해 개정된 기준을 짚는다.
    if v == VERIFY_OK:
        if not t.get(VERIFY_DATE_KEY) or not t.get(VERIFY_BASIS_KEY):
            warnings.append(
                f"확정 트리인데 `{VERIFY_DATE_KEY}`/`{VERIFY_BASIS_KEY}` 가 없습니다 — "
                "기준이 개정돼도 알 수 없습니다. `design_stamp` 로 지금 판을 기록하세요")
        elif check_refs:
            changed, missing = basis_drift(t)
            if changed:
                errors.append(
                    "★확정 이후 인용 기준이 **개정**되었습니다 — " + " · ".join(changed)
                    + f". 새 판으로 다시 확인하고 `{VERIFY_BASIS_KEY}` 를 갱신하세요"
                    " (확정 자체가 유효하지 않을 수 있습니다)")
            if missing:
                warnings.append(
                    f"`{VERIFY_BASIS_KEY}` 에 없는 인용 기준이 있습니다 — " + " · ".join(missing)
                    + ". 확정 후 인용이 늘었거나 기록이 낡았습니다")

    # ★라우팅 표 대조 — 표가 드는 한계상태 중 트리에 없는 것을 잡는다
    if check_refs:
        warnings += missing_limit_states(t, refs)

    # `기계검사통과` 라고 적어 놓고 실제로 못 통과했으면 그 주장 자체가 오류다
    if v == VERIFY_MACHINE and errors:
        errors.insert(0, f"`검증: {VERIFY_MACHINE}` 인데 아래 문제가 남아 있습니다 — "
                         "고치거나 `draft` 로 내리세요")

    return {"errors": errors, "warnings": warnings, "refs": refs, "gaps": gaps}


TEMPLATE = """부재: {member}
단면: {shape}
설계법: {method}          # 한계상태설계법(LRFD) / 허용응력설계법(ASD) — ★설계법이 다르면 근거 기준이 다르다
근거: KDS __ __ __        # 주 출처. 설계법에 맞는 기준을 쓴다
목표검토: "여기에 최종 판정식 (예: φc·Pn ≥ Pu)"

입력:
  - {{기호: __, 설명: __, 단위: __}}   # 엑셀 입력 셀이 된다

흐름:
  - 단계: "1"
    이름: __
    근거: "KDS __ __ __  0.0.0 + 표 0.0-0"   # ★실재하는 절·표를 적는다. design_validate 가 확인한다
    작업: 판정                                # 판정 / 계산 / 산정
    식위치: "0.0.0 — 식은 원문 이미지"        # ★값을 박지 않는다. '어느 절·표의 식'만 가리킨다
    분기:
      # 같은 트리 안으로 — `다음` 은 실재하는 단계 식별자여야 한다
      - {{조건: __, 결과: __, 다음: "2A"}}
      # ★다른 트리로 이어질 때 — 범위 밖이면 끊지 말고 넘긴다
      - {{조건: __, 결과: __, 다음트리: "부재 / 단면"}}   # 설계법 생략 시 이 트리의 설계법을 물려받는다
      # 단면을 가리지 않는 트리로 이을 때는 단면을 비운다 — "부재" · "부재 / / 설계법"
      # ★다른 기준으로 이어질 때
      - {{조건: __, 결과: __, 다음기준: "KDS __ __ __  0.0.0"}}
    출력: __

  - 단계: "2A"
    이름: __
    근거: "KDS __ __ __  0.0.0"
    작업: 산정
    출력: __

안전주의: >
  이 부재 특유의 주의. 값·식은 원문에서 확인하고 계산·최종판단은 설계자가 한다.

검증: draft   # 설계자가 확인한 뒤 '설계자확정' 으로 올린다. 그 전엔 흐름·엑셀에 경고가 붙는다
# 확정한 뒤에는 `design_stamp(path=...)` 로 `검증일`·`검증기준`(그때 기준 판)을 박는다.
# 기준이 개정되면 design_validate 가 "구판으로 확정된 트리" 라고 잡아낸다.
"""


def template(member: str, shape: str, method: str = "한계상태설계법 (하중저항계수설계법, LRFD)") -> str:
    return TEMPLATE.format(member=member or "__", shape=shape or "__", method=method)

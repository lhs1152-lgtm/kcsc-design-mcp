# -*- coding: utf-8 -*-
"""KCSC(국가건설기준센터) OpenAPI 클라이언트.

엔드포인트 (2026-08-05 실측):
    GET {BASE}/CodeList?key={KEY}                  → 카탈로그 3,572건
    GET {BASE}/CodeViewer/{Type}/{Code}?key={KEY}  → 본문 (Type·Code 는 경로, key 는 소문자 쿼리)

함정과 대응:
  · TLS      — 검증을 **기본으로 한다**. 실패하면 조용히 우회하지 않고 KCSC_INSECURE=1 을 안내한다.
  · 코드표기 — "KDS 14 31 10" · "14 31 10" · "143110" 을 모두 받아 6/8자리로 정규화한다.
  · 코드충돌 — 6자리 코드는 종류가 다르면 겹친다(KDS 561건·SMCS 561건). 종류를 못 정하면
               후보를 되돌려 주고 사용자가 고르게 한다. 임의로 하나를 고르지 않는다.
  · umbrella — `xx0000` 류 상위 분류 노드는 본문(list)이 없다. 그렇다고 말해 준다.
"""
from __future__ import annotations

import json
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from . import config


class KcscError(Exception):
    """사용자에게 그대로 보여 줄 수 있는 오류. 원인과 다음 행동을 함께 담는다."""


_TYPE_RE = re.compile(r"^\s*(" + "|".join(config.CODE_TYPES) + r")\b", re.I)


def normalize_code(text: str) -> tuple[str | None, str]:
    """'KDS 14 31 10' → ('KDS', '143110'). 종류가 없으면 (None, '143110').

    숫자만 6자리 또는 8자리로 추린다. 그 밖의 길이는 오류.
    """
    s = (text or "").strip()
    if not s:
        raise KcscError("기준 코드가 비어 있습니다. 예: `KDS 14 31 10` 또는 `143110`")
    code_type = None
    m = _TYPE_RE.match(s)
    if m:
        code_type = m.group(1).upper()
        s = s[m.end():]
    digits = re.sub(r"\D", "", s)
    if len(digits) not in (6, 8):
        raise KcscError(
            f"기준 코드는 숫자 6자리 또는 8자리입니다 (받은 값: {text!r} → 숫자 {len(digits)}자리).\n"
            "예: `KDS 14 31 10` · `14 31 10` · `143110`"
        )
    return code_type, digits


# ── HTTP ────────────────────────────────────────────────────────────────────
def _ssl_context() -> ssl.SSLContext:
    if config.insecure():
        return ssl._create_unverified_context()
    return ssl.create_default_context()


def _fetch(path: str) -> Any:
    key = config.api_key()
    if not key:
        raise KcscError(
            "KCSC 인증키가 없습니다. 환경변수 `KCSC_API_KEY` 에 넣어 주세요.\n"
            "키 발급: 국가건설기준센터 https://kcsc.re.kr → OpenAPI 신청"
        )
    url = f"{config.BASE_URL}/{path}?key={urllib.parse.quote(key, safe='')}"
    req = urllib.request.Request(url, headers={"User-Agent": "kcsc-mcp"})
    try:
        with urllib.request.urlopen(req, timeout=config.timeout(), context=_ssl_context()) as r:
            raw = r.read().decode("utf-8", "replace")
    except ssl.SSLCertVerificationError as e:
        raise KcscError(
            "KCSC 서버의 TLS 인증서를 검증하지 못했습니다.\n"
            f"  원인: {e}\n"
            "이 환경에서 검증이 실패한다면 인증서 체인이 시스템 CA 번들에 없는 것입니다.\n"
            "읽기 전용 공공 API 라 위험은 낮지만 **자동으로 우회하지 않습니다.**\n"
            "우회하려면 환경변수 `KCSC_INSECURE=1` 을 직접 켜 주세요."
        ) from e
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise KcscError(f"인증키가 거부되었습니다 (HTTP {e.code}). `KCSC_API_KEY` 값을 확인해 주세요.") from e
        raise KcscError(f"KCSC 서버 응답 오류 HTTP {e.code} — {e.reason}") from e
    except urllib.error.URLError as e:
        raise KcscError(f"KCSC 서버에 연결하지 못했습니다: {e.reason}") from e
    except TimeoutError as e:
        raise KcscError(f"KCSC 서버 응답이 {config.timeout()}초 안에 오지 않았습니다.") from e
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise KcscError(f"KCSC 응답을 JSON 으로 읽지 못했습니다: {raw[:200]}") from e
    _reject_null_sentinel(data)
    return data


def _reject_null_sentinel(data: Any) -> None:
    """★인증 실패를 잡아낸다.

    KCSC 는 키가 틀려도 **HTTP 200 에 필드가 전부 null 인 껍데기 한 건**을 돌려준다.
    (`[{"no":0,"codeType":null,"code":null,...}]`) 오류코드도 message 도 없다.
    그냥 두면 빈 카탈로그가 캐시에 박혀 이후 모든 조회가 조용히 "못 찾음"이 된다.
    """
    if isinstance(data, list) and len(data) == 1:
        one = data[0]
        if isinstance(one, dict) and one.get("no") == 0 and one.get("codeType") is None:
            raise KcscError(
                "인증키가 거부되었습니다. `KCSC_API_KEY` 값을 확인해 주세요.\n"
                "(KCSC 는 키가 틀려도 오류코드 대신 빈 레코드를 돌려줍니다 — 그래서 여기서 막습니다.)"
            )


# ── 디스크 캐시 ──────────────────────────────────────────────────────────────
def _cache_path(name: str) -> Path:
    d = config.cache_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / name


def _cache_read(name: str, ttl: int) -> Any | None:
    p = _cache_path(name)
    try:
        if time.time() - p.stat().st_mtime > ttl:
            return None
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _cache_write(name: str, data: Any) -> None:
    try:
        _cache_path(name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass  # 캐시는 있으면 좋은 것. 못 써도 동작은 해야 한다.


# ── 카탈로그 ────────────────────────────────────────────────────────────────
def catalog(refresh: bool = False) -> list[dict]:
    """전체 기준 목록. 한 건: no·codeType·code·fullCode·name·version·updateDate·listParentCodes."""
    if not refresh:
        cached = _cache_read("catalog.json", config.catalog_ttl())
        if _looks_like_catalog(cached):
            return cached
    data = _fetch("CodeList")
    if not _looks_like_catalog(data):
        raise KcscError(f"카탈로그 응답이 온전하지 않습니다 (받은 형태: {type(data).__name__}). 잠시 후 다시 시도해 주세요.")
    _cache_write("catalog.json", data)
    return data


def _looks_like_catalog(data: Any) -> bool:
    """온전한 카탈로그인가. 반쪽짜리를 캐시에 박아 두지 않기 위한 문지기."""
    return (isinstance(data, list) and len(data) > 100
            and isinstance(data[0], dict) and data[0].get("codeType"))


def parent_names(entry: dict) -> list[str]:
    """상위 분류 이름들 (`listParentCodes`). 검색어 매칭과 맥락 표시에 쓴다."""
    return [p.get("name") for p in (entry.get("listParentCodes") or []) if p.get("name")]


def find(code: str, code_type: str | None = None) -> list[dict]:
    """코드로 카탈로그 항목을 찾는다. 종류를 안 주면 후보 여럿이 나올 수 있다."""
    t, digits = normalize_code(code)
    want = (code_type or t or "").upper() or None
    hits = [e for e in catalog() if str(e.get("code")) == digits]
    if want:
        hits = [e for e in hits if str(e.get("codeType", "")).upper() == want]
    return hits


def resolve(code: str, code_type: str | None = None) -> tuple[dict, str]:
    """카탈로그 항목 한 건으로 확정한다. → (항목, 알림문)

    ★6자리 코드는 종류가 다르면 그대로 겹친다. `143110` 하나가 KDS(강구조 부재 설계기준)·
    KCS·SMCS·EXCS·LHCS(모두 '제작') 5곳에 있다. 흔한 일이라 오류로 막으면 못 쓴다.
    그래서 **국가기준(KDS→KCS)을 먼저 고르되, 골랐다는 사실과 나머지 후보를 반드시 알린다.**
    조용히 고르지 않는다 — 다른 기준을 읽고 설계하면 그게 사고다.
    """
    t, digits = normalize_code(code)
    want = (code_type or t or "").upper() or None
    if want and want not in config.CODE_TYPES:
        raise KcscError(f"모르는 기준 종류 {want!r}. 가능: {' · '.join(config.CODE_TYPES)}")
    hits = find(code, want)
    if not hits:
        label = f"{want} {digits}" if want else f"코드 {digits}"
        raise KcscError(f"{label} 를 카탈로그에서 찾지 못했습니다. `kcsc_search` 로 먼저 찾아 주세요.")
    if len(hits) == 1:
        return hits[0], ""
    order = {t_: i for i, t_ in enumerate(config.CODE_TYPES)}
    hits.sort(key=lambda h: order.get(str(h.get("codeType")).upper(), 99))
    chosen, rest = hits[0], hits[1:]
    opts = " · ".join(f"`{h['codeType']} {h['code']}` {h.get('name')}" for h in rest)
    note = (f"> ℹ️ 코드 `{digits}` 는 여러 종류에 있습니다. **{chosen['codeType']}** 로 읽었습니다.\n"
            f"> 다른 후보: {opts}\n"
            f"> 다른 것을 보려면 `code_type` 을 지정하세요.")
    return chosen, note


# ── 본문 ────────────────────────────────────────────────────────────────────
def _no_body(entry: dict) -> KcscError:
    ctype, ccode = entry["codeType"], str(entry["code"])
    return KcscError(
        f"{ctype} {ccode} 「{entry.get('name')}」 에는 본문이 없습니다.\n"
        "상위 분류(umbrella) 노드이거나 본문이 공개되지 않은 항목입니다 — "
        "하위 기준을 `kcsc_search` 로 찾아 주세요."
    )


def document(code: str, code_type: str | None = None, refresh: bool = False) -> tuple[dict, str]:
    """기준 본문 문서. → (문서, 알림문). `list` 항목: no·sort·title·level·label·contents(HTML)."""
    entry, note = resolve(code, code_type)
    ctype, ccode = entry["codeType"], str(entry["code"])
    name = f"doc_{ctype}_{ccode}.json"
    if not refresh:
        cached = _cache_read(name, config.doc_ttl())
        if cached and cached.get("version") == entry.get("version"):
            return cached, note
    data = _fetch(f"CodeViewer/{ctype}/{ccode}")
    if isinstance(data, list):
        data = data[0] if data else None
    if not isinstance(data, dict) or not data.get("list"):
        raise _no_body(entry)
    _cache_write(name, data)
    return data, note

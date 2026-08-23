# -*- coding: utf-8 -*-
"""환경설정 — 인증키·캐시 위치·TLS 정책.

값은 전부 환경변수로 받는다. 패키지 안에 키도 데이터도 넣지 않는다.
"""
import os
from pathlib import Path

BASE_URL = "https://kcsc.re.kr/OpenApi"

#: CodeList 가 실제로 담고 있는 기준 종류 (2026-08-05 실측: 9종 3,572건).
#: handoff 문서에는 KDS/KCS 2종으로 적혀 있었으나 기관별 전문시방서가 함께 들어온다.
CODE_TYPES = ("KDS", "KCS", "SMCS", "LHCS", "EXCS", "KRCCS", "KWCS", "NHCS", "KRACS")

CODE_TYPE_NAMES = {
    "KDS": "설계기준",
    "KCS": "표준시방서",
    "SMCS": "서울시 전문시방서",
    "LHCS": "LH 전문시방서",
    "EXCS": "한국도로공사 전문시방서",
    "KRCCS": "한국철도공단 전문시방서",
    "KWCS": "한국수자원공사 전문시방서",
    "NHCS": "한국농어촌공사 전문시방서",
    "KRACS": "한국공항공사 전문시방서",
}


def _flag(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip())
    except (TypeError, ValueError):
        return default


def home() -> Path:
    """사용자 데이터 루트. 캐시·결정트리가 여기 들어간다."""
    p = os.environ.get("KCSC_HOME")
    return Path(p).expanduser() if p else Path.home() / ".kcsc-mcp"


def cache_dir() -> Path:
    return home() / "cache"


def flows_dir() -> Path:
    """사용자 결정트리 폴더. 각 회사가 자기 트리를 넣는 곳."""
    p = os.environ.get("KCSC_FLOWS_DIR")
    return Path(p).expanduser() if p else home() / "flows"


#: ★기본 분야. **말이 없으면 교량으로 본다.**
#
#  건축 강구조(KDS 14 3x)와 교량(KDS 24 xx)은 설계법 계열이 통째로 다르다.
#    교량   한계상태설계법  KDS 24 14 31 강교   (+ 하중조합 24 12 11, 설계하중 24 12 21)
#    교량   허용응력설계법  KDS 24 14 30 강교
#    건축   하중저항계수설계법 KDS 14 31 xx / 허용응력설계법 KDS 14 30 xx
#  개념으로는 LRFD 도 한계상태설계법의 하나지만 **기준 이름으로는 별개 계열**이라,
#  섞으면 교량 설계자에게 건축 기준을 내주게 된다. 하중조합부터 다르다.
#  그래서 분야를 명시하지 않으면 교량으로 두고, 건축은 명시할 때만 나온다.
DOMAIN_DEFAULT = "교량"

#: 분야별 기본 설계법. 분야만 정해지면 설계법도 따라온다.
DOMAIN_METHODS = {
    "교량": "한계상태설계법",
    "건축": "하중저항계수설계법",
}

#: KDS 코드 앞자리 → 분야. (기준 체계의 대분류를 그대로 쓴다)
CODE_DOMAINS = {
    "24": "교량", "14": "건축", "41": "건축", "11": "지반·기초",
    "21": "가설", "27": "터널", "44": "도로", "47": "철도", "51": "하천",
}


def default_domain() -> str:
    v = (os.environ.get("KCSC_DOMAIN") or "").strip()
    return v or DOMAIN_DEFAULT


def domain_of_code(code: str) -> str:
    return CODE_DOMAINS.get(str(code)[:2], "기타")


def api_key() -> str:
    """KCSC 인증키. 없으면 빈 문자열 — 호출 시점에 안내 메시지로 막는다."""
    return (os.environ.get("KCSC_API_KEY") or "").strip()


def insecure() -> bool:
    """TLS 검증 우회 여부. **기본은 검증함.** 명시적 옵트인일 때만 끈다."""
    return _flag("KCSC_INSECURE", False)


def timeout() -> int:
    return _int("KCSC_TIMEOUT", 90)


def catalog_ttl() -> int:
    """카탈로그 캐시 수명(초). 기본 24시간."""
    return _int("KCSC_CATALOG_TTL", 24 * 3600)


def doc_ttl() -> int:
    """본문 캐시 수명(초). 기본 7일 — 기준 개정은 자주 없다."""
    return _int("KCSC_DOC_TTL", 7 * 24 * 3600)


def max_chars() -> int:
    """도구 한 번의 출력 상한(문자). 표 하나가 4만자인 조항도 있다."""
    return _int("KCSC_MAX_CHARS", 20000)

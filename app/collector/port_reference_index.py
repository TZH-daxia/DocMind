"""港口主数据索引与确定性匹配（纯本地，不依赖模型）。

匹配顺序与规则来自真实主数据（6394 条）实测踩出来的坑：

- **三字码直通必须最先做**：`PVG` 走名字匹配是 0 命中，`SHA` 走名字匹配会误命中
  AHL/AOG/ARK 一堆记录；不先直通，用户本来正确的三字码反而会被搞坏。
- **用词元匹配而不是任意子串**：`CHINA` 用子串会命中 `SANFRANCISCOCHINABAS`。
- **前缀匹配限定词元长度 ≥4 且候选名也 ≥4**：主数据里有 60 条 english_name
  短于 4 个字符，短名做前缀会大面积误配。
- **国家/地区词元不参与匹配**：`NINGBO, China` 里的 CHINA 只是噪声。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from app.schemas.port import PortCandidate, PortRecord

# 归一化：去空白与标点，统一大写（主数据 english_name 是紧凑大写英文）
_NON_ALNUM = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff]+")
# 词元切分：空白、逗号、连字符、斜杠、括号等
_TOKEN_SPLIT = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff]+")
# 三字码形态
_THREE_CODE = re.compile(r"^[A-Za-z]{3}$")
# 下拉搜索时"像码"的输入：纯 ASCII 字母数字（中文串不能走码前缀匹配）
_CODE_QUERY = re.compile(r"^[0-9A-Za-z]{2,6}$")

# 参与匹配的词元最短长度（单字符词元噪声太大，直接忽略）
MIN_TOKEN_LENGTH = 2
# 允许做前缀匹配的词元/候选名最短长度
MIN_PREFIX_LENGTH = 4

# 国家/地区与常用国名后缀：出现也不参与港口匹配（`NINGBO, China` 的 CHINA）
COUNTRY_TOKENS: frozenset[str] = frozenset(
    {
        "CHINA",
        "CN",
        "GERMANY",
        "DE",
        "USA",
        "US",
        "UNITED",
        "STATES",
        "JAPAN",
        "JP",
        "KOREA",
        "KR",
        "INDIA",
        "IN",
        "VIETNAM",
        "VN",
        "THAILAND",
        "TH",
        "SINGAPORE",
        "SG",
        "MALAYSIA",
        "MY",
        "INDONESIA",
        "ID",
        "PHILIPPINES",
        "PH",
        "FRANCE",
        "FR",
        "ITALY",
        "IT",
        "SPAIN",
        "ES",
        "NETHERLANDS",
        "NL",
        "BELGIUM",
        "BE",
        "POLAND",
        "PL",
        "TURKEY",
        "TR",
        "RUSSIA",
        "RU",
        "BRAZIL",
        "BR",
        "MEXICO",
        "MX",
        "CANADA",
        "CA",
        "AUSTRALIA",
        "AU",
        "UK",
        "ENGLAND",
        "BRITAIN",
        "HONGKONG",
        "TAIWAN",
        "MACAU",
        # 中文国家/地区与通用地理词：输入"德国""中国"这类词不是港口，
        # 本地直接判非港口，不再浪费一次模型调用
        "中国",
        "德国",
        "日本",
        "韩国",
        "美国",
        "法国",
        "英国",
        "意大利",
        "西班牙",
        "荷兰",
        "比利时",
        "波兰",
        "土耳其",
        "俄罗斯",
        "巴西",
        "墨西哥",
        "加拿大",
        "澳大利亚",
        "泰国",
        "新加坡",
        "马来西亚",
        "印度尼西亚",
        "印度",
        "越南",
        "菲律宾",
        "瑞士",
        "瑞典",
        "奥地利",
        "香港",
        "台湾",
        "澳门",
        "欧洲",
        "亚洲",
        "海外",
        "国内",
    }
)

# 明显不是地名的词（费用/贸易条款/表头类）：命中即判非港口，连模型都不用调
NON_PORT_TOKENS: frozenset[str] = frozenset(
    {
        "FOB",
        "CIF",
        "CFR",
        "EXW",
        "FCA",
        "CPT",
        "CIP",
        "DDP",
        "DAP",
        "OTHER",
        "OTHERS",
        "CHARGE",
        "CHARGES",
        "FEE",
        "FEES",
        "TOTAL",
        "AMOUNT",
        "RATE",
        "PREPAID",
        "COLLECT",
        "DECLARATION",
        "REMARK",
        "REMARKS",
        "NVOCC",
        "MAWB",
        "HAWB",
        "AWB",
    }
)

# 通用后缀/修饰词元：机场港口名里很常见，参与匹配会大量误命中
# （实测 "Frankfurt Intl" 的 INTL 会前缀命中 INTLFALLS）
NOISE_TOKENS: frozenset[str] = frozenset(
    {
        "INTL",
        "INTERNATIONAL",
        "AIRPORT",
        "AIRFIELD",
        "AIRBASE",
        "PORT",
        "SEAPORT",
        "CITY",
        "TOWN",
        "STATION",
        "TERMINAL",
        "APT",
        "GENERAL",
        "AVIATION",
        "CIVIL",
        "MUNICIPAL",
    }
)

# 含这些中文片段的输入不是地名
NON_PORT_HINTS: tuple[str, ...] = (
    "费用",
    "费",
    "条款",
    "声明",
    "合计",
    "其他",
    "预付",
    "到付",
    "备注",
    "表头",
    "签字",
    "盖章",
)

# "像地名"判定用的长度上限：再长基本是描述性文本
MAX_PLACE_LENGTH = 40


def normalize_port_text(text: str) -> str:
    """归一化：去掉空白与标点、统一大写（保留中文）。"""

    return _NON_ALNUM.sub("", str(text or "")).upper()


def tokenize_port_text(text: str) -> list[str]:
    """按非字母数字切分词元（用于词元级匹配，避免任意子串误命中）。"""

    return [token for token in _TOKEN_SPLIT.split(str(text or "").upper()) if token]


def looks_like_place(raw_value: str) -> bool:
    """粗判输入是否"像港口名"，决定要不要为它付一次模型调用。

    只做保守判定：明显是费用/条款/表头词、或长度异常的直接排除；其余放行去
    问模型——宁可多问一次，也不要把真实港口错误地判成非港口。
    """

    text = str(raw_value or "").strip()
    if not text or len(text) > MAX_PLACE_LENGTH:
        return False
    if any(hint in text for hint in NON_PORT_HINTS):
        return False
    tokens = tokenize_port_text(text)
    if not tokens or len(tokens) > 3:
        return False
    meaningful = [
        token
        for token in tokens
        if len(token) >= MIN_TOKEN_LENGTH
        and token not in COUNTRY_TOKENS
        and token not in NOISE_TOKENS
    ]
    if not meaningful:
        # 例如 GERMANY、CHINA 这类国家名：不是港口，也不需要问模型
        return False
    # 剩下的词元全是贸易条款/费用词（FOB、CHARGE）时同样判定为非地名
    return not all(token in NON_PORT_TOKENS for token in meaningful)


# 单次返回的候选上限：极端输入（常见词前缀）可能命中上百条，
# 超出部分舍弃但把真实总数带回去，由前端折叠展示
MAX_CANDIDATES = 20

# 搜索结果的匹配级别：三字码前缀 > 名称整串相等 > 名称前缀 > 名称包含。
# 用户在下拉框里既可能敲码（PV）也可能敲城市名（SHANGHAI），两条索引都要用上。
_RANK_NAME_EXACT = 0
_RANK_CODE_PREFIX = 1
_RANK_NAME_PREFIX = 2
_RANK_NAME_CONTAINS = 3


@dataclass(frozen=True)
class PortLookupResult:
    """一次本地匹配的结果。"""

    kind: Literal["unique", "ambiguous", "not_found"]
    candidates: tuple[PortCandidate, ...] = ()
    matched_by: str | None = None
    # 命中的候选总数（可能大于 candidates 的长度）
    total: int = 0


class PortReferenceIndex:
    """港口主数据索引：构建一次，支持反复查询。"""

    def __init__(self, records: list[PortRecord]) -> None:
        self._by_code: dict[str, PortRecord] = {}
        self._by_name: dict[str, list[PortRecord]] = {}
        for record in records:
            code = (record.three_code or "").strip().upper()
            if code:
                self._by_code[code] = record
            name_key = normalize_port_text(record.english_name)
            if name_key:
                self._by_name.setdefault(name_key, []).append(record)

    @property
    def size(self) -> int:
        """主数据记录数（按三字码去重）。"""

        return len(self._by_code)

    def lookup(self, raw_value: str) -> PortLookupResult:
        """按"三字码直通 → 整串相等 → 词元相等 → 词元前缀"依次匹配。"""

        text = str(raw_value or "").strip()
        if not text:
            return PortLookupResult("not_found")
        # 0) 三字码直通：用户本来就填了码（含小写）
        if _THREE_CODE.match(text):
            record = self._by_code.get(text.upper())
            if record is not None:
                return PortLookupResult(
                    "unique", (self._to_candidate(record),), "code"
                )
        # 1) 整串归一化后相等（SHANGHAIPUDONG、Frankfurt）
        exact = self._by_name.get(normalize_port_text(text))
        if exact:
            return self._finish(exact, "name_exact")
        # 2) 词元相等（跳过国家与通用后缀词元）
        hits: list[PortRecord] = []
        matched_by: str | None = None
        tokens = [
            token
            for token in tokenize_port_text(text)
            if len(token) >= MIN_TOKEN_LENGTH
            and token not in COUNTRY_TOKENS
            and token not in NOISE_TOKENS
        ]
        for token in tokens:
            equal = self._by_name.get(token)
            if equal:
                hits.extend(equal)
                matched_by = matched_by or "name_token"
        if hits:
            return self._finish(hits, matched_by or "name_token")
        # 3) 前缀匹配：只认首个有意义词元（`SHANGHAI` 这种未限定城市名）。
        #    逐个词元做前缀会让 "Frankfurt Intl" 的 INTL 命中 INTLFALLS
        head = tokens[0] if tokens else ""
        if len(head) >= MIN_PREFIX_LENGTH:
            for name, records in self._by_name.items():
                if len(name) < MIN_PREFIX_LENGTH:
                    continue
                if name.startswith(head) or head.startswith(name):
                    hits.extend(records)
                    matched_by = matched_by or "name_prefix"
        if hits:
            return self._finish(hits, matched_by or "name_prefix")
        return PortLookupResult("not_found")

    def search(
        self, keyword: str, limit: int = MAX_CANDIDATES
    ) -> list[PortCandidate]:
        """按关键字返回候选港口（供前端「输入即下拉」选择）。

        排序优先级：三字码精确 > 三字码前缀 > 名称整串相等 > 名称前缀 > 名称
        包含；同一港口命中多条时取优先级最高的一条。与 lookup 的区别：这里只
        负责列出候选，不做唯一性判定（选哪个由用户决定）。
        """

        text = str(keyword or "").strip()
        key = normalize_port_text(text)
        if not key:
            return []
        scored: dict[str, tuple[tuple[int, str], PortCandidate]] = {}
        # 1) 三字码：精确直通优先（用户/调用方直接给了码）。不限定 3 位，
        #    少数码长不一致的主数据也能被反查到
        code_key = text.upper()
        record = self._by_code.get(code_key)
        if record is not None:
            return [self._to_candidate(record)]
        # 2) 码前缀：只对纯 ASCII 字母数字生效（中文串走名称匹配）
        if _CODE_QUERY.match(code_key):
            for code, record in self._by_code.items():
                if code.startswith(code_key):
                    self._keep(scored, self._to_candidate(record), (_RANK_CODE_PREFIX, code))
        # 2) 名称：太短的关键字会命中一大片，单字符不参与
        if len(key) >= MIN_TOKEN_LENGTH:
            for name_key, records in self._by_name.items():
                rank = self._name_rank(key, name_key)
                if rank is None:
                    continue
                for record in records:
                    self._keep(
                        scored, self._to_candidate(record), (rank, name_key)
                    )
        ordered = sorted(scored.values(), key=lambda item: item[0])
        return [candidate for _, candidate in ordered[: max(1, limit)]]

    @staticmethod
    def _name_rank(key: str, name_key: str) -> int | None:
        """返回关键字与主数据名称的匹配级别；不匹配返回 None。"""

        if name_key == key:
            return _RANK_NAME_EXACT
        if name_key.startswith(key):
            return _RANK_NAME_PREFIX
        if key in name_key:
            return _RANK_NAME_CONTAINS
        return None

    @staticmethod
    def _keep(
        scored: dict[str, tuple[tuple[int, str], PortCandidate]],
        candidate: PortCandidate,
        score: tuple[int, str],
    ) -> None:
        """按三字码去重，只保留优先级更高的一条记录。"""

        if not candidate.three_code:
            return
        current = scored.get(candidate.three_code)
        if current is None or score < current[0]:
            scored[candidate.three_code] = (score, candidate)

    def _finish(self, records: list[PortRecord], matched_by: str) -> PortLookupResult:
        unique: dict[str, PortCandidate] = {}
        for record in records:
            code = (record.three_code or "").strip().upper()
            if code:
                unique.setdefault(code, self._to_candidate(record))
        candidates = tuple(list(unique.values())[:MAX_CANDIDATES])
        if len(candidates) == 1:
            return PortLookupResult("unique", candidates, matched_by, len(candidates))
        return PortLookupResult(
            "ambiguous", candidates, matched_by, len(unique)
        )

    @staticmethod
    def _to_candidate(record: PortRecord) -> PortCandidate:
        return PortCandidate(
            three_code=(record.three_code or "").strip().upper(),
            english_name=record.english_name or "",
            country_code=record.country_code or "",
        )

"""预报尺寸备注（`ybvolumeremark`）的确定性判定。

订单里要的是**单个「长*宽*高」**。托书里的尺寸写法五花八门，所以只把标准写法当正常，
其余一律转人工核对，并把原文（模型抽出来的值）留给操作员确认。

判为正常（标准写法）：

    30*50*50        30 * 50 * 50        30x50x50        30×50×50
    60*40*40cm      长30*宽50*高50      L30*W50*H50     尺寸：30*50*50

判为需人工核对（返回 False）：

    - 多一段件数：`38*27*25CM/9`、`30*50*50*4`
    - 非「长*宽*高」相乘：`30cm 50cm 50cm`、`30,50,50`、`200*300`
    - 多个物品尺寸：值里含换行（多行尺寸），如 `60*40*40cm` 与 `60*40*30cm` 两行
    - 其他归不出来的写法（含长宽高之外的数字、多个尺寸连写等）

为什么不交给提示词让模型自己判：这条规则是确定的、可枚举的，代码判既稳定又能被测试锁住；
模型只负责"把原文照抽出来"（见 prompts/po_order_extraction.md）。
"""

import re
from typing import Any

DIMENSION_REMARK_KEY = "ybvolumeremark"

# 标准写法：三个数（可带小数）用乘号连接；每段可带 长/宽/高 或 L/W/H 前缀；
# 末尾可带长度单位。整体必须从头到尾匹配（多一段、多一行都会失配）。
_STANDARD_PATTERN = re.compile(
    r"^\s*(?:长|L)?\s*(\d+(?:[.,]\d+)?)\s*[*x×✕＊]\s*"
    r"(?:宽|W)?\s*(\d+(?:[.,]\d+)?)\s*[*x×✕＊]\s*"
    r"(?:高|H)?\s*(\d+(?:[.,]\d+)?)\s*"
    r"(?:mm|cm|m|厘米|毫米|米)?\.?\s*$",
    re.IGNORECASE,
)

# 值前面常见的标签：模型可能连标签一起抽出来，判定前先剥掉一层
_LABEL_PATTERN = re.compile(
    r"^\s*(?:尺寸|规格|箱规|货物尺寸|体积及货物尺寸|体积|"
    r"size|dimension|dimensions|measurement)\s*[:：]?\s*",
    re.IGNORECASE,
)


def is_standard_dimension_remark(text: Any) -> bool:
    """值是否是「单个长*宽*高」的标准写法（只有它算正常）。

    空值返回 False，但调用方应按「字段缺失」处理，不要因此把它计入待审核
    （托书里本来就没有尺寸的情况很常见，见 build_result 里的用法）。
    """

    if text is None:
        return False
    value = str(text).strip()
    if not value:
        return False

    # 多行一定有多个物品尺寸，先显式排掉，别依赖 $ 对换行的语义
    if "\n" in value or "\r" in value:
        return False

    value = _LABEL_PATTERN.sub("", value, count=1)
    return bool(_STANDARD_PATTERN.match(value))

"""外协窑对账：一位小数良率反推、整批总账约束与达标判定。

业务口径
--------
外协窑只上报烧成件数 ``fired`` 和四舍五入到一位小数的良率
``reported_yield``（百分数）。合格件数必须是 0..fired 的整数，因此一条
上报对应一个整数区间，而不是一个点。厂内总账给出整批合格件数后，再用
“各窑区间之和必须等于总账”的约束收窄每一窑的区间。

后补单窑实数时，该窑不再按上报区间推算，整批约束以实数扣减；其余窑的
区间随总账重新收窄。所有区间计算使用整数/有理数，避免浮点边界导致四舍
五入判断漂移。
"""
from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation
from fractions import Fraction

MAX_FIRED = 1_000_000_000


class InputError(ValueError):
    """可直接展示给业务人员的输入错误。"""


def parse_reported_yield(value) -> tuple[int, str]:
    """把上报良率解析为“千分位整数”(tenths of a percent)。

    返回 ``(tenths, canonical)``，例如 ``92.3`` -> ``(923, "92.3")``。
    只接受最多一位小数、0–100 之间的有限数值；布尔值视为非法。
    """
    if isinstance(value, bool) or value is None:
        raise InputError("上报良率必须为 0–100 之间的数值，最多一位小数")
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise InputError("上报良率必须为 0–100 之间的数值，最多一位小数")
    if not number.is_finite() or number < 0 or number > 100:
        raise InputError("上报良率必须为 0–100 之间的数值，最多一位小数")
    scaled = number * 10
    if scaled != scaled.to_integral_value():
        raise InputError("上报良率只保留一位小数，请勿提交更多位数")
    tenths = int(scaled)
    return tenths, f"{Decimal(tenths) / 10:.1f}"


def reported_interval(fired: int, tenths: int) -> tuple[int, int]:
    """一位小数良率在给定烧成件数下对应的整数合格件数区间（闭区间）。

    四舍五入到一位小数（half-up）意味着良率（百分数）落在
    ``[t-0.05, t+0.05)``，下端含、上端不含。换算成合格件数：
    ``fired*(2t-1) <= 2000*passed < fired*(2t+1)``。
    """
    if fired <= 0:
        raise InputError("烧成件数必须为正整数")
    numerator_lo = fired * (2 * tenths - 1)
    numerator_hi = fired * (2 * tenths + 1)
    low = -((-numerator_lo) // 2000)          # ceil
    high = -((-numerator_hi) // 2000) - 1     # ceil(...) - 1，上端不含
    return max(0, low), min(fired, high)


def required_passed(fired: int, target) -> int:
    """达到目标良率所需的最少合格件数（向上取整）。"""
    return math.ceil(Fraction(str(target)) * fired / 100)


def _as_int(value, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool):
        raise InputError(f"{label}必须为整数")
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise InputError(f"{label}必须为整数")
    if not number.is_finite() or number != number.to_integral_value() or number < minimum:
        raise InputError(f"{label}必须为不小于 {minimum} 的整数")
    return int(number)


def _rate(value, label: str) -> Decimal:
    if isinstance(value, bool):
        raise InputError(f"{label}必须为数值")
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise InputError(f"{label}必须为数值")
    if not number.is_finite() or not 0 < number <= 100:
        raise InputError(f"{label}须在 0–100 之间")
    return number


def compute(items, ledger_total=None, target=93.0) -> dict:
    """计算对账明细。

    ``items`` 为字典序列，字段：``batch``、``line``、``fired``、
    ``reported_yield``（原始上报值）、``supplied_passed``（后补实数，可空）。
    ``ledger_total`` 为空表示尚未拿到厂内总账，只做复盘试算。
    """
    ledger = None if ledger_total is None else _as_int(ledger_total, "总账合格件数")
    if not items:
        raise InputError("至少需要一条外协窑上报")
    target_rate = _rate(target, "目标良率")

    seen = set()
    rows = []

    for index, raw in enumerate(items):
        batch = str(raw.get("batch") or "").strip()
        line = str(raw.get("line") or "").strip()
        if not batch:
            raise InputError(f"第 {index + 1} 条缺少窑次编号")
        if batch in seen:
            raise InputError(f"窑次 {batch} 重复上报")
        seen.add(batch)
        fired = _as_int(raw.get("fired"), f"窑次 {batch} 的烧成件数", minimum=1)
        if fired > MAX_FIRED:
            raise InputError(f"窑次 {batch} 的烧成件数超出支持范围")
        tenths, canonical = parse_reported_yield(raw.get("reported_yield"))
        low, high = reported_interval(fired, tenths)
        issues = []
        if low > high:
            # 上报组合不可实现时没有可用的区间，不能拿截断值参与整批合计。
            issues.append(
                f"上报良率 {canonical}% 与烧成件数 {fired} 无法对应任何整数合格件数，请核对"
            )
            low = high = None

        supplied_value = None
        if raw.get("supplied_passed") is not None:
            supplied_value = _as_int(raw.get("supplied_passed"), f"窑次 {batch} 的补报合格件数")
            if supplied_value > fired:
                issues.append(f"补报合格件数 {supplied_value} 超过烧成件数 {fired}")
            elif low is not None and not (low <= supplied_value <= high):
                issues.append(
                    f"补报合格件数 {supplied_value} 对应良率 "
                    f"{supplied_value / fired * 100:.1f}%，与上报 {canonical}% 不一致"
                )

        rows.append(
            {
                "batch": batch,
                "line": line,
                "fired": fired,
                "reported_yield": canonical,
                "reported_min": low,
                "reported_max": high,
                "supplied_passed": supplied_value,
                "required_passed": required_passed(fired, target_rate),
                "effective_low": supplied_value if supplied_value is not None else low,
                "effective_high": supplied_value if supplied_value is not None else high,
                "issues": issues,
            }
        )

    total_fired = sum(r["fired"] for r in rows)
    blocking = []
    bad_reports = [r for r in rows if r["reported_min"] is None]
    unresolved = [r for r in rows if r["effective_low"] is None]
    if unresolved:
        total_min = total_max = None
        feasible = False
        total_note = (
            f"{len(unresolved)} 个窑次的上报良率与烧成件数无法对应整数合格件数（"
            + "、".join(r["batch"] for r in unresolved)
            + "），整批区间无法确定，请先核对上报。"
        )
    else:
        total_min = sum(r["effective_low"] for r in rows)
        total_max = sum(r["effective_high"] for r in rows)
        if ledger is None:
            feasible = None
            total_note = "总账未到，当前仅按各窑上报区间做复盘试算。"
            blocking.append("厂内总账尚未给出，整批合格件数未对账，不能批准。")
        elif ledger > total_fired:
            feasible = False
            total_note = f"总账 {ledger} 件超过整批烧成件数 {total_fired} 件，对不上，请核对总账。"
        elif ledger < total_min:
            feasible = False
            total_note = (
                f"总账 {ledger} 件低于各窑上报允许的最少 {total_min} 件，少 {total_min - ledger} 件；"
                "请核对总账或上报良率。"
            )
        elif ledger > total_max:
            feasible = False
            total_note = (
                f"总账 {ledger} 件高于各窑上报允许的最多 {total_max} 件，多 {ledger - total_max} 件；"
                "请核对总账或上报良率。"
            )
        else:
            feasible = True
            total_note = f"总账 {ledger} 件落在各窑可解析区间 {total_min}–{total_max} 件内，整批有解。"

    if feasible is not True:
        blocking.append(total_note)

    for row in rows:
        low, high = row["effective_low"], row["effective_high"]
        if low is None or high is None:
            row["constrained_min"] = row["constrained_max"] = None
            row["constrained"] = False
        elif feasible is True:
            other_min = total_min - row["effective_low"]
            other_max = total_max - row["effective_high"]
            row["constrained_min"] = max(row["effective_low"], ledger - other_max)
            row["constrained_max"] = min(row["effective_high"], ledger - other_min)
        else:
            row["constrained_min"] = low
            row["constrained_max"] = high
        low, high, required = row["constrained_min"], row["constrained_max"], row["required_passed"]
        row["constrained"] = low is not None and low <= high
        supplied = row["supplied_passed"]

        if row["issues"]:
            row["verdict"] = "待核"
        elif supplied is not None:
            # 补报实数后该窑不再依赖整批推算，单窑结论按实数给出；
            # 整批能否批准仍取决于总账是否对得上。
            row["verdict"] = "达标" if supplied >= required else "未达标"
            row["reason"] = f"已补报实数 {supplied} 件，达标线 {required} 件"
        elif bad_reports:
            row["verdict"] = "待核"
            if low is not None:
                row["issues"].append(
                    f"另有窑次上报无法解析，整批推算暂停；该窑上报区间为 {low}–{high} 件"
                )
        elif feasible is False:
            row["verdict"] = "待核"
            row["issues"].append(
                f"整批总账无解，整批推算失效；该窑上报区间为 {low}–{high} 件"
            )
        elif low is None or low > high:
            row["verdict"] = "待核"
            row["issues"].append("总账约束后该窑区间为空，请核对上报与总账")
        elif high < required:
            row["verdict"] = "未达标"
            row["reason"] = f"可能 {low}–{high} 件，均低于达标线 {required} 件"
        elif low >= required:
            row["verdict"] = "达标"
            if low == high:
                row["reason"] = (
                    f"总账约束下确定为 {low} 件，达标线 {required} 件"
                    if feasible is True
                    else f"单窑确定为 {low} 件，达标线 {required} 件"
                )
            else:
                row["reason"] = f"可能 {low}–{high} 件，均不低于达标线 {required} 件"
        else:
            row["verdict"] = "待核"
            row["issues"].append(
                f"可能 {low}–{high} 件，跨越达标线 {required} 件，"
                + ("总账未到，无法确定" if ledger is None else "整批约束下仍无法确定")
            )
        row["reason"] = "；".join(row["issues"]) if row["issues"] else row.get("reason", "")
        if row["verdict"] == "待核" and feasible is not False:
            blocking.append(f"{row['batch']}：{row['reason']}")

    undetermined = [r for r in rows if r["verdict"] == "待核"]
    can_approve = ledger is not None and feasible is True and not undetermined and not blocking
    if bad_reports:
        phase = "needs_input"
    elif ledger is None:
        phase = "trial"
    elif feasible is False:
        phase = "infeasible"
    else:
        phase = "reconciled"

    return {
        "target": float(target_rate),
        "ledger_total": ledger,
        "phase": phase,
        "feasible": feasible,
        "total": {
            "min": total_min,
            "max": total_max,
            "fired": total_fired,
            "ledger": ledger,
            "note": total_note,
        },
        "summary": {
            "item_count": len(rows),
            "determined": len(rows) - len(undetermined),
            "undetermined": len(undetermined),
            "compliant": sum(r["verdict"] == "达标" for r in rows),
            "non_compliant": sum(r["verdict"] == "未达标" for r in rows),
        },
        "can_approve": can_approve,
        "blocking_reasons": blocking,
        "items": rows,
    }

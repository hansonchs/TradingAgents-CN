from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel, Field
from typing import Literal, Optional, Dict, Any, List, Tuple
from datetime import datetime
import logging
import math
import re

from app.routers.auth_db import get_current_user
from app.core.database import get_mongo_db
from app.core.response import ok

router = APIRouter(prefix="/paper", tags=["paper"])
logger = logging.getLogger("webapi")


# 每个市场的初始资金配置
INITIAL_CASH_BY_MARKET = {
    "CNY": 1_000_000.0,   # A股：100万人民币
    "HKD": 1_000_000.0,   # 港股：100万港币
    "USD": 100_000.0      # 美股：10万美元
}


MAX_QUANTITY = 2**63 - 1


class PlaceOrderRequest(BaseModel):
    code: str = Field(..., description="股票代码（支持A股/港股/美股）")
    side: Literal["buy", "sell"]
    quantity: int = Field(..., gt=0, le=MAX_QUANTITY)
    market: Optional[str] = Field(None, description="市场类型 (CN/HK/US)，不传则自动识别")
    # 可选：关联的分析ID，便于从分析页面一键下单后追踪
    analysis_id: Optional[str] = None


def _detect_market_and_code(code: str) -> Tuple[str, str]:
    """
    检测股票代码的市场类型并标准化代码

    Returns:
        (market, normalized_code): 市场类型和标准化后的代码
            - CN: A股（6位数字）
            - HK: 港股（4-5位数字或带.HK后缀）
            - US: 美股（字母代码）
    """
    code = code.strip().upper()

    # 港股：带 .HK 后缀
    if code.endswith('.HK'):
        return ('HK', code[:-3].zfill(5))

    # 美股：纯字母
    if re.match(r'^[A-Z]+$', code):
        return ('US', code)

    # 港股：4-5位数字
    if re.match(r'^\d{4,5}$', code):
        return ('HK', code.zfill(5))

    # A股：6位数字
    if re.match(r'^\d{6}$', code):
        return ('CN', code)

    # 默认当作A股，补齐6位
    return ('CN', code.zfill(6))


async def _get_or_create_account(user_id: str) -> Dict[str, Any]:
    """获取或创建账户（多货币）"""
    db = get_mongo_db()
    acc = await db["paper_accounts"].find_one({"user_id": user_id})
    if not acc:
        now = datetime.utcnow().isoformat()
        acc = {
            "user_id": user_id,
            # 多货币现金账户
            "cash": {
                "CNY": INITIAL_CASH_BY_MARKET["CNY"],
                "HKD": INITIAL_CASH_BY_MARKET["HKD"],
                "USD": INITIAL_CASH_BY_MARKET["USD"]
            },
            # 多货币已实现盈亏
            "realized_pnl": {
                "CNY": 0.0,
                "HKD": 0.0,
                "USD": 0.0
            },
            # 账户设置
            "settings": {
                "auto_currency_conversion": False,
                "default_market": "CN"
            },
            "created_at": now,
            "updated_at": now,
        }
        await db["paper_accounts"].insert_one(acc)
    return acc


async def _get_market_rules(market: str) -> Optional[Dict[str, Any]]:
    """获取市场规则配置"""
    db = get_mongo_db()
    rules_doc = await db["paper_market_rules"].find_one({"market": market})
    if rules_doc:
        return rules_doc.get("rules", {})
    return None


def _calculate_commission(market: str, side: str, amount: float, rules: Dict[str, Any]) -> float:
    """计算手续费"""
    if not rules or "commission" not in rules:
        return 0.0

    commission_config = rules["commission"]
    if not isinstance(commission_config, dict):
        raise ValueError("invalid commission configuration")
    fields = {"rate", "min", "stamp_duty_rate", "transaction_levy_rate",
              "trading_fee_rate", "settlement_fee_rate", "sec_fee_rate"}
    commission_config = dict(commission_config)
    for field in fields.intersection(commission_config):
        value = _finite_number(commission_config[field])
        if value is None or value < 0:
            raise ValueError("invalid commission configuration")
        commission_config[field] = value
    commission = 0.0

    # 佣金
    comm_rate = commission_config.get("rate", 0.0)
    comm_min = commission_config.get("min", 0.0)
    commission += max(amount * comm_rate, comm_min)

    # 印花税（仅卖出）
    if side == "sell" and "stamp_duty_rate" in commission_config:
        commission += amount * commission_config["stamp_duty_rate"]

    # 其他费用（港股）
    if market == "HK":
        if "transaction_levy_rate" in commission_config:
            commission += amount * commission_config["transaction_levy_rate"]
        if "trading_fee_rate" in commission_config:
            commission += amount * commission_config["trading_fee_rate"]
        if "settlement_fee_rate" in commission_config:
            commission += amount * commission_config["settlement_fee_rate"]

    # SEC费用（美股，仅卖出）
    if market == "US" and side == "sell" and "sec_fee_rate" in commission_config:
        commission += amount * commission_config["sec_fee_rate"]

    return round(commission, 2)


async def _get_available_quantity(user_id: str, code: str, market: str) -> int:
    """获取可用数量（考虑T+1限制）"""
    db = get_mongo_db()
    pos = await db["paper_positions"].find_one({"user_id": user_id, "code": code})

    if not pos:
        return 0

    total_qty = pos.get("quantity", 0)

    # A股T+1：今天买入的不能卖出
    if market == "CN":
        # 获取市场规则
        rules = await _get_market_rules(market)
        if rules and rules.get("t_plus", 0) > 0:
            # 查询今天的买入数量
            today = datetime.utcnow().date().isoformat()
            pipeline = [
                {"$match": {
                    "user_id": user_id,
                    "code": code,
                    "side": "buy",
                    "timestamp": {"$gte": today}
                }},
                {"$group": {"_id": None, "total": {"$sum": "$quantity"}}}
            ]
            today_buy = await db["paper_trades"].aggregate(pipeline).to_list(1)
            today_buy_qty = today_buy[0]["total"] if today_buy else 0
            return max(0, total_qty - today_buy_qty)

    # 港股/美股T+0：全部可用
    return total_qty


def _finite_number(value: Any) -> Optional[float]:
    """Normalize numbers without admitting booleans, NaN or infinities."""
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _positive_price(value: Any) -> Optional[float]:
    number = _finite_number(value)
    return number if number is not None and number > 0 else None


def _finite_amount(value: Any) -> Optional[float]:
    number = _finite_number(value)
    return round(number, 2) if number is not None else None


def _valid_quantity(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= MAX_QUANTITY else None


def _position_value(last: Any, qty: Optional[int], avg_cost: Optional[float]) -> Dict[str, Any]:
    price = _positive_price(last)
    market_value = 0.0 if qty == 0 else (_finite_amount(price * qty) if price is not None and qty is not None else None)
    pnl = 0.0 if qty == 0 else (_finite_amount((price - avg_cost) * qty) if price is not None and avg_cost is not None and qty is not None else None)
    return {
        "last_price": price,
        "market_value": market_value,
        "unrealized_pnl": pnl,
        "valuation_status": "available" if market_value is not None and pnl is not None else "unavailable",
    }


async def _get_last_price(code: str, market: str) -> Optional[float]:
    """
    获取股票最新价格（支持多市场）

    Args:
        code: 股票代码
        market: 市场类型 (CN/HK/US)

    Returns:
        最新价格，如果获取失败返回 None
    """
    db = get_mongo_db()

    # A股：从数据库获取
    if market == "CN":
        # 1. 尝试从 market_quotes 获取
        q = await db["market_quotes"].find_one(
            {"$or": [{"code": code}, {"symbol": code}]},
            {"_id": 0, "close": 1}
        )
        price = _positive_price(q.get("close")) if q else None
        if price is not None:
            return price

        # 2. 回退到 stock_basic_info 的 current_price
        basic_info = await db["stock_basic_info"].find_one(
            {"$or": [{"code": code}, {"symbol": code}]},
            {"_id": 0, "current_price": 1}
        )
        price = _positive_price(basic_info.get("current_price")) if basic_info else None
        if price is not None:
            return price

        logger.error(f"❌ 无法从数据库获取A股价格: {code}")
        return None

    # 港股/美股：使用 ForeignStockService
    elif market in ['HK', 'US']:
        try:
            from app.services.foreign_stock_service import ForeignStockService
            db = get_mongo_db()
            service = ForeignStockService(db=db)

            quote = await service.get_quote(market, code, force_refresh=False)

            if quote:
                for field in ("price", "current_price", "close"):
                    price = _positive_price(quote.get(field))
                    if price is not None:
                        return price
        except Exception as e:
            logger.error(f"❌ 获取{market}股价格失败 {code}: {e}")
            return None

    logger.error(f"❌ 无法获取股票价格: {code} (market={market})")
    return None


def _zfill_code(code: str) -> str:
    s = str(code).strip()
    if len(s) == 6 and s.isdigit():
        return s
    return s.zfill(6)


@router.get("/account", response_model=dict)
async def get_account(current_user: dict = Depends(get_current_user)):
    """获取或创建纸上账户，返回资金与持仓估值汇总（支持多市场）"""
    db = get_mongo_db()
    acc = await _get_or_create_account(current_user["id"])

    # 聚合持仓估值（按货币分类）
    positions = await db["paper_positions"].find({"user_id": current_user["id"]}).to_list(None)

    positions_value_by_currency = {
        "CNY": 0.0,
        "HKD": 0.0,
        "USD": 0.0
    }

    detailed_positions: List[Dict[str, Any]] = []
    incomplete_currencies = set()
    for p in positions:
        code = p.get("code")
        market = p.get("market", "CN")
        currency = p.get("currency", "CNY")
        qty = _valid_quantity(p.get("quantity"))
        avg_cost = _finite_number(p.get("avg_cost"))
        if avg_cost is not None and avg_cost < 0:
            avg_cost = None
        available_qty = _valid_quantity(p.get("available_qty", qty))

        # 获取最新价
        last = await _get_last_price(code, market)
        valuation = _position_value(last, qty, avg_cost)
        mkt_value = valuation["market_value"]
        if valuation["valuation_status"] == "unavailable":
            incomplete_currencies.add(currency)
        current_value = positions_value_by_currency[currency]
        positions_value_by_currency[currency] = (
            _finite_amount(current_value + mkt_value)
            if current_value is not None and mkt_value is not None else None
        )

        detailed_positions.append({
            "code": code,
            "market": market,
            "currency": currency,
            "quantity": qty,
            "available_qty": available_qty,
            "avg_cost": avg_cost,
            **valuation
        })

    # 计算总资产（按货币分别显示）
    cash = acc.get("cash", {})
    realized_pnl = acc.get("realized_pnl", {})

    # 兼容旧格式（单一现金）
    if not isinstance(cash, dict):
        cash = {"CNY": _finite_number(cash), "HKD": None, "USD": None}
    if not isinstance(realized_pnl, dict):
        realized_pnl = {"CNY": _finite_number(realized_pnl), "HKD": None, "USD": None}

    cash_values = {currency: _finite_amount(cash.get(currency)) for currency in INITIAL_CASH_BY_MARKET}
    pnl_values = {currency: _finite_amount(realized_pnl.get(currency)) for currency in INITIAL_CASH_BY_MARKET}
    equity = {
        currency: _finite_amount(cash_values[currency] + value)
        if cash_values[currency] is not None and value is not None else None
        for currency, value in positions_value_by_currency.items()
    }
    summary = {
        "cash": cash_values,
        "realized_pnl": pnl_values,
        "positions_value": positions_value_by_currency,
        "equity": equity,
        "valuation_status": {
            currency: "available" if equity[currency] is not None
            and pnl_values[currency] is not None and currency not in incomplete_currencies else "unavailable"
            for currency in INITIAL_CASH_BY_MARKET
        },
        "updated_at": acc.get("updated_at"),
    }

    return ok({"account": summary, "positions": detailed_positions})


@router.post("/order", response_model=dict)
async def place_order(payload: PlaceOrderRequest, current_user: dict = Depends(get_current_user)):
    """提交市价单，按最新价即时成交（支持多市场）"""
    db = get_mongo_db()

    # 1. 识别市场类型
    if payload.market:
        market = payload.market.upper()
        normalized_code = payload.code
    else:
        market, normalized_code = _detect_market_and_code(payload.code)

    side = payload.side
    qty = int(payload.quantity)
    analysis_id = getattr(payload, "analysis_id", None)

    # 2. 确定货币
    currency_map = {
        "CN": "CNY",
        "HK": "HKD",
        "US": "USD"
    }
    currency = currency_map.get(market, "CNY")

    # Validate quote and amounts before creating or mutating an account.
    price = _positive_price(await _get_last_price(normalized_code, market))
    if price is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"无法获取股票 {normalized_code} ({market}) 的最新价格"
        )

    # 5. 计算金额
    try:
        notional = _finite_amount(price * qty)
    except OverflowError:
        notional = None
    if notional is None or notional <= 0:
        raise HTTPException(status_code=400, detail="无效的模拟成交金额")

    # 6. 获取市场规则并计算手续费
    rules = await _get_market_rules(market)
    try:
        commission = _calculate_commission(market, side, notional, rules) if rules else 0.0
    except (TypeError, ValueError, OverflowError):
        raise HTTPException(status_code=400, detail="无效的模拟成交费用")
    total_cost = notional + commission
    if not math.isfinite(commission) or commission < 0 or not math.isfinite(total_cost):
        raise HTTPException(status_code=400, detail="无效的模拟成交费用")

    acc = await _get_or_create_account(current_user["id"])

    # 7. 获取持仓
    pos = await db["paper_positions"].find_one({"user_id": current_user["id"], "code": normalized_code})

    now_iso = datetime.utcnow().isoformat()
    realized_pnl_delta = 0.0

    # Validate existing state and calculate all mutations before the first write.
    cash = acc.get("cash", {})
    pnl_balances = acc.get("realized_pnl", {})
    available_cash = _finite_number(cash.get(currency) if isinstance(cash, dict) else cash if currency == "CNY" else None)
    current_pnl = _finite_number(pnl_balances.get(currency) if isinstance(pnl_balances, dict) else pnl_balances if currency == "CNY" else None)
    old_qty = _valid_quantity(pos.get("quantity")) if pos else 0
    stored_available = _valid_quantity(pos.get("available_qty", old_qty)) if pos else 0
    old_cost = _finite_number(pos.get("avg_cost")) if pos else price
    if (available_cash is None or available_cash < 0 or current_pnl is None
            or old_qty is None or stored_available is None or old_cost is None or old_cost < 0):
        raise HTTPException(status_code=409, detail="模拟账户或持仓数据异常，请先核对数据")

    if side == "buy":
        if available_cash < total_cost:
            raise HTTPException(status_code=400, detail=f"可用{currency}不足")
        new_cash = _finite_amount(available_cash - total_cost)
        new_qty = old_qty + qty
        if new_qty > MAX_QUANTITY:
            raise HTTPException(status_code=409, detail="模拟持仓数量超出范围")
        try:
            average = (old_cost * old_qty + price * qty) / new_qty
            new_avg = round(average, 4) if math.isfinite(average) else None
        except OverflowError:
            new_avg = None
        new_available = stored_available if pos and market == "CN" else (0 if market == "CN" else new_qty)
        if new_cash is None or new_avg is None:
            raise HTTPException(status_code=409, detail="模拟交易计算结果无效")
        position_update = dict(quantity=new_qty, available_qty=new_available, avg_cost=new_avg, updated_at=now_iso)
        account_updates = {f"cash.{currency}": new_cash} if isinstance(cash, dict) else {
            "cash": {"CNY": new_cash, "HKD": None, "USD": None}}
        if not isinstance(pnl_balances, dict):
            account_updates["realized_pnl"] = {"CNY": current_pnl, "HKD": None, "USD": None}
        await db["paper_accounts"].update_one(
            {"user_id": current_user["id"]},
            {"$set": {**account_updates, "updated_at": now_iso}}
        )
        if not pos:
            await db["paper_positions"].insert_one(dict(
                user_id=current_user["id"], code=normalized_code, market=market,
                currency=currency, frozen_qty=0, **position_update))
        else:
            await db["paper_positions"].update_one({"_id": pos["_id"]}, {"$set": position_update})
    else:
        available_qty = await _get_available_quantity(current_user["id"], normalized_code, market)
        if not pos or available_qty < qty or old_qty < qty:
            raise HTTPException(status_code=400, detail="可用持仓不足")
        new_qty = old_qty - qty
        realized_pnl_delta = _finite_amount((price - old_cost) * qty)
        net_proceeds = _finite_amount(notional - commission)
        new_cash = _finite_amount(available_cash + net_proceeds) if net_proceeds is not None else None
        updated_pnl = _finite_amount(current_pnl + realized_pnl_delta) if realized_pnl_delta is not None else None
        new_available = max(0, stored_available - qty)
        if new_cash is None or new_cash < 0 or updated_pnl is None:
            raise HTTPException(status_code=409, detail="模拟交易计算结果无效")
        account_updates = {"$set": {"updated_at": now_iso}}
        increments = {}
        for field, balance, value, delta in (
            ("cash", cash, new_cash, net_proceeds),
            ("realized_pnl", pnl_balances, updated_pnl, realized_pnl_delta),
        ):
            if isinstance(balance, dict):
                increments[f"{field}.{currency}"] = delta
            else:
                account_updates["$set"][field] = {"CNY": value, "HKD": None, "USD": None}
        if increments:
            account_updates["$inc"] = increments
        await db["paper_accounts"].update_one({"user_id": current_user["id"]}, account_updates)
        if new_qty == 0:
            await db["paper_positions"].delete_one({"_id": pos["_id"]})
        else:
            await db["paper_positions"].update_one(
                {"_id": pos["_id"]},
                {"$set": dict(quantity=new_qty, available_qty=new_available, updated_at=now_iso)})

    # 9. 记录订单与成交（即成）
    order_doc = {
        "user_id": current_user["id"],
        "code": normalized_code,
        "market": market,
        "currency": currency,
        "side": side,
        "quantity": qty,
        "price": price,
        "amount": notional,
        "commission": commission,
        "status": "filled",
        "created_at": now_iso,
        "filled_at": now_iso,
    }
    if analysis_id:
        order_doc["analysis_id"] = analysis_id
    await db["paper_orders"].insert_one(order_doc)

    trade_doc = {
        "user_id": current_user["id"],
        "code": normalized_code,
        "market": market,
        "currency": currency,
        "side": side,
        "quantity": qty,
        "price": price,
        "amount": notional,
        "commission": commission,
        "pnl": realized_pnl_delta if side == "sell" else 0.0,
        "timestamp": now_iso,
    }
    if analysis_id:
        trade_doc["analysis_id"] = analysis_id
    await db["paper_trades"].insert_one(trade_doc)

    return ok({"order": {k: v for k, v in order_doc.items() if k != "_id"}})


@router.get("/positions", response_model=dict)
async def list_positions(current_user: dict = Depends(get_current_user)):
    """获取持仓列表（支持多市场）"""
    db = get_mongo_db()
    items = await db["paper_positions"].find({"user_id": current_user["id"]}).to_list(None)
    enriched: List[Dict[str, Any]] = []
    for p in items:
        code = p.get("code")
        market = p.get("market", "CN")
        currency = p.get("currency", "CNY")
        qty = _valid_quantity(p.get("quantity"))
        available_qty = _valid_quantity(p.get("available_qty", qty))
        avg_cost = _finite_number(p.get("avg_cost"))
        if avg_cost is not None and avg_cost < 0:
            avg_cost = None

        last = await _get_last_price(code, market)
        valuation = _position_value(last, qty, avg_cost)
        enriched.append({
            "code": code,
            "market": market,
            "currency": currency,
            "quantity": qty,
            "available_qty": available_qty,
            "avg_cost": avg_cost,
            **valuation
        })
    return ok({"items": enriched})


@router.get("/orders", response_model=dict)
async def list_orders(limit: int = Query(50, ge=1, le=200), current_user: dict = Depends(get_current_user)):
    db = get_mongo_db()
    cursor = db["paper_orders"].find({"user_id": current_user["id"]}).sort("created_at", -1).limit(limit)
    items = await cursor.to_list(None)
    # 去除 _id
    cleaned = [{k: v for k, v in it.items() if k != "_id"} for it in items]
    return ok({"items": cleaned})


@router.post("/reset", response_model=dict)
async def reset_account(confirm: bool = Query(False), current_user: dict = Depends(get_current_user)):
    """重置账户（支持多货币）"""
    if not confirm:
        raise HTTPException(status_code=400, detail="请设置 confirm=true 以确认重置")
    db = get_mongo_db()
    await db["paper_accounts"].delete_many({"user_id": current_user["id"]})
    await db["paper_positions"].delete_many({"user_id": current_user["id"]})
    await db["paper_orders"].delete_many({"user_id": current_user["id"]})
    await db["paper_trades"].delete_many({"user_id": current_user["id"]})
    # 重新创建账户
    acc = await _get_or_create_account(current_user["id"])
    return ok({"message": "账户已重置", "cash": acc.get("cash", {})})
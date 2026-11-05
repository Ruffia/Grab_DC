import os
import pandas as pd
import tushare as ts

ts.set_token("c37889408c22e87e788a7388c75c99bc9b1fc56d3e1f8bb77cf264ae")   # 或直接 ts.set_token("你的token")
pro = ts.pro_api()

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
全市场个股资金流 -> SQLite 数据库 Tip.IndividualFundFlow_DC（表已存在，只写数据）
================================================================================

前提：数据库与 IndividualFundFlow_DC 表已经建好（脚本不再执行 DDL，只做字段校验）。

数据源：Tushare Pro  moneyflow_dc（东方财富口径，需 5000 积分，数据自 2023-09-11）
表结构（与已有表一致）：
    TradeDate DATE, StockCode TEXT, StockName TEXT, Price real, Rise real,
    FundFlow_MainForce real, FundFlow_SuperLargeOrder real, FundFlow_LargeOrder real,
    FundFlow_MediumOrder real, FundFlow_SmallOrder real,
    PRIMARY KEY(TradeDate, StockCode)
    说明：7 个数值列全部为 REAL，入库后可直接参与比较、排序、SUM/AVG。

字段对应关系：
    TradeDate                <- trade_date        （转成 YYYY-MM-DD）
    StockCode                <- ts_code
    StockName                <- name
    Price                    <- close             最新价
    Rise                     <- pct_change        涨跌幅（%）
    FundFlow_MainForce       <- net_amount        今日主力净流入额（万元）
    FundFlow_SuperLargeOrder <- buy_elg_amount    今日超大单净流入额
    FundFlow_LargeOrder      <- buy_lg_amount     今日大单净流入额
    FundFlow_MediumOrder     <- buy_md_amount     今日中单净流入额
    FundFlow_SmallOrder      <- buy_sm_amount     今日小单净流入额

数值处理：所有金额、价格、涨跌幅统一保留 1 位小数（四舍五入 ROUND_HALF_UP），
          NULL 仍写 NULL。

用法：
    python stock_moneyflow_dc_to_db.py                  # 最近 15 个交易日，全市场
    python stock_moneyflow_dc_to_db.py --days 5
    python stock_moneyflow_dc_to_db.py --date 20241101
    python stock_moneyflow_dc_to_db.py --start 20241101 --end 20241115
    python stock_moneyflow_dc_to_db.py --db "Tip.db"

依赖：pip install tushare pandas
"""

import argparse
import math
import os
import sqlite3
import time
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

import pandas as pd
import tushare as ts

TOKEN = os.getenv("TUSHARE_TOKEN", "")
SLEEP = 0.35                    # 逐日请求间隔
TABLE = "IndividualFundFlow_DC"
DECIMALS = 1                    # 保留 1 位小数
_QUANT = Decimal("0.1")         # 与 DECIMALS 对应

# 表必须包含的字段（用于启动时校验）
REQUIRED_COLUMNS = [
    "TradeDate", "StockCode", "StockName", "Price", "Rise",
    "FundFlow_MainForce", "FundFlow_SuperLargeOrder", "FundFlow_LargeOrder",
    "FundFlow_MediumOrder", "FundFlow_SmallOrder",
]

# 应当是数值类型的列（校验用，避免被误建/误改回 TEXT）
NUMERIC_COLUMNS = [
    "Price", "Rise", "FundFlow_MainForce", "FundFlow_SuperLargeOrder",
    "FundFlow_LargeOrder", "FundFlow_MediumOrder", "FundFlow_SmallOrder",
]

# 幂等写入：主键冲突时更新其余字段，重复跑同一天不会报错
UPSERT = f'''
INSERT INTO "{TABLE}" (
    "TradeDate", "StockCode", "StockName", "Price", "Rise",
    "FundFlow_MainForce", "FundFlow_SuperLargeOrder", "FundFlow_LargeOrder",
    "FundFlow_MediumOrder", "FundFlow_SmallOrder"
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT("TradeDate", "StockCode") DO UPDATE SET
    "StockName"                = excluded."StockName",
    "Price"                    = excluded."Price",
    "Rise"                     = excluded."Rise",
    "FundFlow_MainForce"       = excluded."FundFlow_MainForce",
    "FundFlow_SuperLargeOrder" = excluded."FundFlow_SuperLargeOrder",
    "FundFlow_LargeOrder"      = excluded."FundFlow_LargeOrder",
    "FundFlow_MediumOrder"     = excluded."FundFlow_MediumOrder",
    "FundFlow_SmallOrder"      = excluded."FundFlow_SmallOrder"
'''


# --------------------------------------------------------------------------- #
# Tushare 取数
# --------------------------------------------------------------------------- #
def get_pro(token: str = ""):
    """初始化 pro_api，token 优先级：参数 > 环境变量 > 已保存的 token。"""
    token = (token or TOKEN).strip()
    if token:
        ts.set_token(token)
    try:
        return ts.pro_api()
    except Exception as exc:
        raise SystemExit(
            "初始化 Tushare 失败：%s\n"
            "请到 https://tushare.pro/user/token 获取 token 后重试。" % exc)


def norm_date(value: str) -> str:
    """2024-11-01 / 2024/11/01 / 20241101 -> 20241101。"""
    text = str(value).strip().replace("-", "").replace("/", "")
    datetime.strptime(text, "%Y%m%d")
    return text


def to_db_date(yyyymmdd: str) -> str:
    """20241101 / 2024-11-01 -> 2024-11-01（SQLite 推荐的日期文本格式）。"""
    text = str(yyyymmdd).strip().replace("-", "").replace("/", "")
    return f"{text[:4]}-{text[4:6]}-{text[6:8]}"


def call_dc(pro, **kwargs) -> pd.DataFrame:
    """调用 moneyflow_dc，处理频率限制与积分不足。"""
    params = {k: v for k, v in kwargs.items() if v is not None}
    last_exc = None
    for attempt in range(3):
        try:
            df = pro.moneyflow_dc(**params)
            return df if df is not None else pd.DataFrame()
        except Exception as exc:
            last_exc = exc
            msg = str(exc)
            if "每分钟" in msg:
                time.sleep(61)
                continue
            if "权限" in msg or "积分" in msg:
                raise SystemExit(f"moneyflow_dc 调用失败（需 5000 积分）：{msg}")
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"moneyflow_dc 调用失败：{last_exc}")


def get_trade_days(pro, start_date: str, end_date: str) -> list:
    """区间内的交易日（升序）。"""
    cal = pro.trade_cal(exchange="SSE", start_date=start_date, end_date=end_date,
                        fields="cal_date,is_open")
    return sorted(cal.loc[cal["is_open"].astype(int) == 1, "cal_date"].astype(str))


def recent_trade_days(pro, days: int, end_date: str = None) -> list:
    """最近 days 个交易日（升序），默认以今天为终点。"""
    end = norm_date(end_date) if end_date else datetime.now().strftime("%Y%m%d")
    span = days * 2 + 10                       # 交易日约占日历日 2/3，留冗余
    start = (datetime.strptime(end, "%Y%m%d") - timedelta(days=span)).strftime("%Y%m%d")
    return get_trade_days(pro, start, end)


def fetch_one_day(pro, trade_date: str) -> pd.DataFrame:
    """取某一天的全市场数据；空结果表示非交易日或尚未更新。"""
    return call_dc(pro, trade_date=trade_date)


def fetch_days(pro, dates: list) -> pd.DataFrame:
    """
    逐日拉取全市场并拼接。某天返回空（如当天盘后还没更新）时自动往前补一天，
    保证拿到 len(dates) 个有效交易日的数据。
    """
    dates = list(dates)
    extra = 10                                  # 最多向前补 10 个交易日
    probe_start = (datetime.strptime(dates[0], "%Y%m%d")
                   - timedelta(days=extra * 2 + 10)).strftime("%Y%m%d")
    candidates = get_trade_days(pro, probe_start, dates[-1])
    pool = [d for d in candidates if d < dates[0]] + dates

    frames, got = [], 0
    for day in reversed(pool):                  # 从最新往前取
        if got >= len(dates):
            break
        df = fetch_one_day(pro, day)
        if df.empty:
            print(f"  {day} 无数据（未更新或非交易日），跳过并向前补一天")
        else:
            frames.append(df)
            got += 1
        print(f"\r  已取 {got}/{len(dates)} 个交易日  当前 {day}", end="", flush=True)
        time.sleep(SLEEP)
    print()

    if not frames:
        return pd.DataFrame()

    out = pd.concat(frames, ignore_index=True)
    out = out.drop_duplicates(subset=["trade_date", "ts_code"], keep="last")
    return out.sort_values(["trade_date", "ts_code"]).reset_index(drop=True)


# --------------------------------------------------------------------------- #
# 数据库
# --------------------------------------------------------------------------- #
def connect(db_path: str) -> sqlite3.Connection:
    """连接已有数据库（不会建库建表）。"""
    if not os.path.exists(db_path):
        raise SystemExit(f"找不到数据库文件：{os.path.abspath(db_path)}")
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = true")
    return conn


def check_table(conn: sqlite3.Connection) -> None:
    """校验表、字段与数值列类型，早发现结构漂移。"""
    info = conn.execute(f'PRAGMA table_info("{TABLE}")').fetchall()
    if not info:
        raise SystemExit(f'数据库中不存在表 "{TABLE}"，请先建表。')

    columns = {row[1]: (row[2] or "").upper() for row in info}
    missing = [c for c in REQUIRED_COLUMNS if c not in columns]
    if missing:
        raise SystemExit(f'表 "{TABLE}" 缺少字段：{", ".join(missing)}')

    numeric_types = ("REAL", "FLOAT", "DOUBLE", "NUMERIC", "INTEGER")
    bad = [c for c in NUMERIC_COLUMNS if columns.get(c) not in numeric_types]
    if bad:
        print(f'[提醒] 以下列不是数值类型，比较/排序会退化成文本：{", ".join(bad)}')
    else:
        print("[信息] 表结构校验通过：10 个字段齐全，7 个数值列均为 REAL")


def _num(value):
    """数值 -> 保留 1 位小数的 float（四舍五入）；None / NaN / 非数字 -> None。"""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return float(Decimal(str(f)).quantize(_QUANT, rounding=ROUND_HALF_UP))


def to_rows(df: pd.DataFrame) -> list:
    """DataFrame -> executemany 用的元组列表，顺序与 UPSERT 一致。"""
    rows = []
    for rec in df.itertuples(index=False):
        rows.append((
            to_db_date(rec.trade_date),
            rec.ts_code,
            rec.name,
            _num(rec.close),            # Price
            _num(rec.pct_change),       # Rise
            _num(rec.net_amount),       # FundFlow_MainForce
            _num(rec.buy_elg_amount),   # FundFlow_SuperLargeOrder
            _num(rec.buy_lg_amount),    # FundFlow_LargeOrder
            _num(rec.buy_md_amount),    # FundFlow_MediumOrder
            _num(rec.buy_sm_amount),    # FundFlow_SmallOrder
        ))
    return rows


def upsert(conn: sqlite3.Connection, rows: list) -> int:
    """在一个事务里批量写入，返回受影响行数。"""
    before = conn.total_changes
    with conn:                                  # 成功自动 commit，异常自动 rollback
        conn.executemany(UPSERT, rows)
    return conn.total_changes - before


def report(conn: sqlite3.Connection, dates: list) -> None:
    """入库结果自检。"""
    total = conn.execute(f'SELECT COUNT(*) FROM "{TABLE}"').fetchone()[0]
    stocks = conn.execute(f'SELECT COUNT(DISTINCT "StockCode") FROM "{TABLE}"').fetchone()[0]
    dmin, dmax = conn.execute(
        f'SELECT MIN("TradeDate"), MAX("TradeDate") FROM "{TABLE}"').fetchone()
    print(f"\n表内合计 {total} 行，{stocks} 只股票，日期范围 {dmin} ~ {dmax}")

    print("\n本次涉及日期的入库行数：")
    placeholders = ",".join("?" * len(dates))
    q = (f'SELECT "TradeDate", COUNT(*) FROM "{TABLE}" '
         f'WHERE "TradeDate" IN ({placeholders}) '
         f'GROUP BY "TradeDate" ORDER BY "TradeDate"')
    for d, n in conn.execute(q, [to_db_date(x) for x in dates]):
        print(f"  {d}  {n} 行")


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="全市场个股资金流入库（moneyflow_dc -> SQLite）")
    p.add_argument("--db", default="Tip.db", help="数据库文件，默认 Tip.db")
    p.add_argument("--days", type=int, default=15, help="最近多少个交易日，默认 15")
    p.add_argument("--date", default=None, help="只取某一天，如 20241101")
    p.add_argument("--start", default=None, help="区间开始日")
    p.add_argument("--end", default=None, help="区间结束日（也用作 --days 的截止日）")
    p.add_argument("--token", default=None, help="Tushare token")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    pro = get_pro(args.token or "")

    conn = connect(args.db)
    try:
        check_table(conn)

        # -------- 确定交易日 --------
        if args.date:
            dates = [norm_date(args.date)]
        elif args.start and args.end:
            dates = get_trade_days(pro, norm_date(args.start), norm_date(args.end))
            if not dates:
                print("[提示] 区间内没有交易日")
                return 0
        else:
            dates = recent_trade_days(pro, args.days, args.end)
            if not dates:
                print("[提示] 未取到交易日，请检查日期范围")
                return 0
        print(f"计划取 {len(dates)} 个交易日：{dates[0]} ~ {dates[-1]}（全市场）")

        # -------- 取数 --------
        raw = fetch_one_day(pro, dates[0]) if len(dates) == 1 else fetch_days(pro, dates)
        if raw.empty:
            print("[提示] 接口返回空：可能是非交易日，或数据尚未更新")
            return 0

        rows = to_rows(raw)
        print(f"\n待写入 {len(rows)} 行（数值保留 {DECIMALS} 位小数），"
              f"数据库：{os.path.abspath(args.db)}")

        # -------- 入库 --------
        changed = upsert(conn, rows)
        print(f"[完成] 写入/更新 {changed} 行")
        report(conn, dates)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
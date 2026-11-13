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

前提：数据库与 IndividualFundFlow_DC 表已经建好（脚本不执行 DDL，只做字段校验）。

取数天数从数据库的 Settings 表读取：
    CREATE TABLE "Settings" (
      "ItemName"  TEXT NOT NULL,
      "ItemValue" TEXT,
      "Description" TEXT,
      PRIMARY KEY ("ItemName")
    );
    例如：INSERT INTO "Settings" VALUES ('Days', '15', '获取多少天(交易日)的资金流数据');
优先级：命令行 --days > Settings 表的 Days 项 > 默认 15 天。
Settings 表不存在、没有 Days 项或值非法时，退回默认 15 天并给出提示。

数据源：Tushare Pro  moneyflow_dc（东方财富口径，需 5000 积分，数据自 2023-09-11）

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

数值处理：Price、Rise 保留 2 位小数；其余 5 个资金流字段保留 1 位小数
          （四舍五入 ROUND_HALF_UP），NULL 仍写 NULL。

用法：
    python stock_moneyflow_dc_to_db.py                  # 天数按 Settings 表来
    python stock_moneyflow_dc_to_db.py --days 5         # 临时覆盖 Settings.Days
    python stock_moneyflow_dc_to_db.py --date 20241101  # 只看某一天
    python stock_moneyflow_dc_to_db.py --start 20241101 --end 20241115
    python stock_moneyflow_dc_to_db.py --db "U:\\Grab\\Tip.db"

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
SETTINGS_TABLE = "Settings"     # 配置表：ItemName / ItemValue
DEFAULT_DAYS = 15               # Settings 缺失时的兜底天数
PRICE_DECIMALS = 2              # Price、Rise 保留 2 位小数
FLOW_DECIMALS = 1               # 5 个资金流字段保留 1 位小数
_Q_PRICE = Decimal("0.01")      # 与 PRICE_DECIMALS 对应
_Q_FLOW = Decimal("0.1")        # 与 FLOW_DECIMALS 对应

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
    """
    最近 days 个交易日（升序），默认以今天为终点。

    先放宽日历区间查出交易日，再截取最后 days 个；区间不够（春节、
    国庆长假）时自动扩大窗口重查，保证数量刚好是 days。
    """
    end = norm_date(end_date) if end_date else datetime.now().strftime("%Y%m%d")
    end_dt = datetime.strptime(end, "%Y%m%d")

    all_days = []
    for span in (days * 2 + 10, days * 4 + 30, days * 6 + 90):
        start = (end_dt - timedelta(days=span)).strftime("%Y%m%d")
        all_days = get_trade_days(pro, start, end)
        if len(all_days) >= days:
            break

    return all_days[-days:]


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
# 数据库 / 配置
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


def read_setting(conn: sqlite3.Connection, name: str):
    """从 Settings 表读一个配置项；表不存在 / 没有该项 / 值为空，都返回 None。"""
    try:
        row = conn.execute(
            f'SELECT "ItemValue" FROM "{SETTINGS_TABLE}" '
            f'WHERE "ItemName" = ? COLLATE NOCASE', (name,)
        ).fetchone()
    except sqlite3.Error:                     # 表不存在
        return None
    if not row or row[0] is None:
        return None
    value = str(row[0]).strip()
    return value or None


def resolve_days(conn: sqlite3.Connection, cli_days):
    """
    取数天数优先级：命令行 --days > Settings.Days > DEFAULT_DAYS。
    返回 (天数, 来源说明)。
    """
    if cli_days is not None:
        if int(cli_days) <= 0:
            raise SystemExit(f"--days 必须是正整数，收到：{cli_days}")
        return int(cli_days), "命令行 --days"

    raw = read_setting(conn, "Days")
    if raw is None:
        print(f"[提醒] Settings 表里没有 Days 项（或表不存在），使用默认 {DEFAULT_DAYS} 天")
        return DEFAULT_DAYS, "默认值"

    try:
        days = int(float(raw))
    except ValueError:
        print(f'[提醒] Settings.Days = "{raw}" 不是有效数字，使用默认 {DEFAULT_DAYS} 天')
        return DEFAULT_DAYS, "默认值"

    if days <= 0:
        print(f'[提醒] Settings.Days = "{raw}" 不是正整数，使用默认 {DEFAULT_DAYS} 天')
        return DEFAULT_DAYS, "默认值"

    return days, f'Settings.Days = "{raw}"'


# --------------------------------------------------------------------------- #
# 加工 / 入库
# --------------------------------------------------------------------------- #
def _num(value, quant):
    """数值 -> 按 quant 指定的位数四舍五入；None / NaN / 非数字 -> None。"""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    v = float(Decimal(str(f)).quantize(quant, rounding=ROUND_HALF_UP))
    return v + 0.0                          # -0.0 归一成 0.0


def to_rows(df: pd.DataFrame) -> list:
    """DataFrame -> executemany 用的元组列表，顺序与 UPSERT 一致。"""
    rows = []
    for rec in df.itertuples(index=False):
        rows.append((
            to_db_date(rec.trade_date),
            rec.ts_code,
            rec.name,
            _num(rec.close, _Q_PRICE),           # Price        2 位小数
            _num(rec.pct_change, _Q_PRICE),      # Rise         2 位小数
            _num(rec.net_amount, _Q_FLOW),       # FundFlow_MainForce       1 位小数
            _num(rec.buy_elg_amount, _Q_FLOW),   # FundFlow_SuperLargeOrder 1 位小数
            _num(rec.buy_lg_amount, _Q_FLOW),    # FundFlow_LargeOrder      1 位小数
            _num(rec.buy_md_amount, _Q_FLOW),    # FundFlow_MediumOrder     1 位小数
            _num(rec.buy_sm_amount, _Q_FLOW),    # FundFlow_SmallOrder      1 位小数
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
    p.add_argument("--days", type=int, default=None,
                   help="临时覆盖 Settings 表的 Days 项；不传则读 Settings")
    p.add_argument("--date", default=None, help="只取某一天，如 20241101")
    p.add_argument("--start", default=None, help="区间开始日")
    p.add_argument("--end", default=None, help="区间结束日（也用作天数的截止日）")
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
            days, source = resolve_days(conn, args.days)
            print(f"[信息] 取数天数：{days}（来源：{source}）")
            dates = recent_trade_days(pro, days, args.end)
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
        print(f"\n待写入 {len(rows)} 行（价格/涨幅 {PRICE_DECIMALS} 位小数，"
              f"资金流 {FLOW_DECIMALS} 位小数），数据库：{os.path.abspath(args.db)}")

        # -------- 入库 --------
        changed = upsert(conn, rows)
        print(f"[完成] 写入/更新 {changed} 行")
        report(conn, dates)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
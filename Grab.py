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

完整性校验（重要）：
    Tushare 当日数据是「逐步放量」的，收盘后可能先只有几百行。
    写库前会和表内上一个交易日比行数，低于 90% 视为未更新完：
      - 默认【不写库】，退出码 2，提示稍后重跑；
      - 加 --allow-partial 可强制写入（补数据时用）。

数值处理：Price、Rise 保留 2 位小数；其余 5 个资金流字段保留 1 位小数
          （四舍五入 ROUND_HALF_UP），NULL 仍写 NULL。

用法：
    python Grab.py                  # 天数按 Settings 表来
    python Grab.py --days 5         # 临时覆盖 Settings.Days
    python Grab.py --date 20241101  # 只看某一天
    python Grab.py --start 20241101 --end 20241115
    python Grab.py --allow-partial  # 数据没更新完也写

依赖：pip install tushare pandas
"""

import argparse
import math
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

import pandas as pd
import tushare as ts

TOKEN = os.getenv("TUSHARE_TOKEN", "")
SLEEP = 0.35                    # 逐日请求间隔
DB_PATH = r"U:\Grab\Tip.db"     # 默认数据库（可用 --db 覆盖）
TABLE = "IndividualFundFlow_DC"
SETTINGS_TABLE = "Settings"     # 配置表：ItemName / ItemValue
DEFAULT_DAYS = 15               # Settings 缺失时的兜底天数
PRICE_DECIMALS = 2              # Price、Rise 保留 2 位小数
FLOW_DECIMALS = 1               # 5 个资金流字段保留 1 位小数
PARTIAL_RATIO = 0.90            # 当日行数 / 上一交易日行数 低于该比例视为未更新完
_Q_PRICE = Decimal("0.01")      # 与 PRICE_DECIMALS 对应
_Q_FLOW = Decimal("0.1")        # 与 FLOW_DECIMALS 对应

API_RETRIES = 4                 # 接口异常（502/网关错误）时的重试次数
API_WAIT = 8                    # 每次重试间隔（秒）
EMPTY_TOLERANCE = 5             # 连续多少个交易日返回空就判定为接口异常

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


def is_api_down(df) -> bool:
    """
    判断返回值是否代表「接口异常」而不是「当天没有数据」。

    Tushare 服务端 502 / 网关故障时，tushare 库不抛异常，而是返回 0 行 0 列的空表；
    正常的「当天无数据」会带着完整字段名返回空表。
    """
    return df is None or (df.empty and len(df.columns) == 0)


def api_down_error(detail: str) -> SystemExit:
    """接口不可用时给出可执行的排查提示，而不是抛 KeyError。"""
    return SystemExit(
        f"Tushare 接口无有效响应：{detail}\n"
        f"已重试 {API_RETRIES} 次（间隔 {API_WAIT}s）。常见原因：\n"
        "  1) Tushare 服务端故障（502 Bad Gateway），过几分钟再跑即可；\n"
        "  2) 本机网络 / 代理拦截，可访问 http://api.tushare.pro 验证；\n"
        "  3) 该接口积分不足（trade_cal 需 2000 分，moneyflow_dc 需 5000 分）。")


def call_dc(pro, **kwargs) -> pd.DataFrame:
    """调用 moneyflow_dc，处理频率限制、积分不足与服务端 502。"""
    params = {k: v for k, v in kwargs.items() if v is not None}
    last_msg = ""
    for attempt in range(1, API_RETRIES + 1):
        try:
            df = pro.moneyflow_dc(**params)
        except Exception as exc:
            last_msg = str(exc)
            if "每分钟" in last_msg:
                time.sleep(61)
                continue
            if "权限" in last_msg or "积分" in last_msg:
                raise SystemExit(f"moneyflow_dc 调用失败（需 5000 积分）：{last_msg}")
            print(f"[警告] 第 {attempt}/{API_RETRIES} 次请求失败：{last_msg[:100]}", file=sys.stderr)
            time.sleep(API_WAIT)
            continue

        if is_api_down(df):
            last_msg = "返回 0 行 0 列（疑似服务端 502 或网关错误）"
            print(f"[警告] 第 {attempt}/{API_RETRIES} 次请求异常：{last_msg}", file=sys.stderr)
            time.sleep(API_WAIT)
            continue
        return df

    raise api_down_error(f"moneyflow_dc {params} 连续 {API_RETRIES} 次失败，最后一次：{last_msg}")


def get_trade_days(pro, start_date: str, end_date: str) -> list:
    """
    区间内的交易日（升序）。

    trade_cal 可能因服务端 502 返回空表，这里显式校验字段并重试，
    避免直接抛 KeyError: 'is_open' 这种误导性错误。
    """
    last_msg = ""
    for attempt in range(1, API_RETRIES + 1):
        try:
            cal = pro.trade_cal(exchange="SSE", start_date=start_date, end_date=end_date,
                                fields="cal_date,is_open")
        except Exception as exc:
            cal = None
            last_msg = str(exc)

        if cal is not None and "is_open" in cal.columns and not cal.empty:
            return sorted(cal.loc[cal["is_open"].astype(int) == 1, "cal_date"].astype(str))

        if cal is not None and "is_open" in cal.columns:
            return []                       # 字段正常但区间内无交易日
        last_msg = last_msg or "trade_cal 返回 0 行 0 列（疑似服务端 502）"
        print(f"[警告] 第 {attempt}/{API_RETRIES} 次获取交易日历失败：{last_msg[:100]}",
              file=sys.stderr)
        if attempt < API_RETRIES:
            time.sleep(API_WAIT)

    raise api_down_error(f"trade_cal {start_date}~{end_date} 连续 {API_RETRIES} 次失败，"
                         f"最后一次：{last_msg}")


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

    frames, got, misses = [], 0, 0
    for day in reversed(pool):                  # 从最新往前取
        if got >= len(dates):
            break
        df = fetch_one_day(pro, day)
        if df.empty:
            misses += 1
            print(f"  {day} 无数据（未更新或非交易日），跳过并向前补一天")
            if misses >= EMPTY_TOLERANCE:
                raise SystemExit(
                    f"连续 {misses} 个交易日都返回空表，接口多半异常（服务端 502 或积分不足），"
                    "已中止以免反复空转。请先用 trade_cal 自检后重试。")
        else:
            misses = 0
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


def check_completeness(conn: sqlite3.Connection, df: pd.DataFrame,
                       allow_partial: bool = False) -> bool:
    """
    写库前的完整性校验：当日行数 vs 表内上一个交易日行数。

    Tushare 当日资金流是逐步放量的（例如 19:30 只有 606 行，而前一交易日有 6035 行），
    低于 PARTIAL_RATIO 视为「还没更新完」，默认拒绝写入。
    """
    if df.empty:
        return True

    dates = sorted(df["trade_date"].astype(str).unique())
    latest = dates[-1]
    today_n = int((df["trade_date"].astype(str) == latest).sum())

    row = conn.execute(
        f'SELECT "TradeDate", COUNT(*) FROM "{TABLE}" WHERE "TradeDate" < ? '
        f'GROUP BY 1 ORDER BY 1 DESC LIMIT 1', (to_db_date(latest),)).fetchone()
    if not row or not row[1]:
        return True                          # 表里还没有历史，无从比较

    prev_day, prev_n = row[0], int(row[1])
    ratio = today_n / prev_n
    if ratio >= PARTIAL_RATIO:
        return True

    print(f"[警告] {to_db_date(latest)} 只取到 {today_n} 行，"
          f"上一交易日 {prev_day} 有 {prev_n} 行（仅 {ratio:.0%}），数据源多半尚未更新完。")
    if allow_partial:
        print("[提示] 已指定 --allow-partial，按现有数据写入。")
        return True

    print("[中止] 未写入数据库。等数据更新完后重跑；确实要写半成品请加 --allow-partial。")
    return False


def upsert(conn: sqlite3.Connection, rows: list, batch: int = 500) -> int:
    """分批写同一个事务，每 batch 条打印一次进度。"""
    total = len(rows)
    if total == 0:
        return 0
    before = conn.total_changes
    done, tty, t0 = 0, sys.stdout.isatty(), time.time()
    with conn:                                  # 成功自动 commit，异常自动 rollback
        for i in range(0, total, batch):
            chunk = rows[i:i + batch]
            conn.executemany(UPSERT, chunk)
            done += len(chunk)
            msg = (f"[入库] {done}/{total} 行（{done * 100.0 / total:5.1f}%），"
                   f"耗时 {time.time() - t0:.1f}s")
            if tty:
                print(f"\r{msg}", end="", flush=True)
            else:
                print(msg, flush=True)
    if tty:
        print()
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
    p.add_argument("--db", default=DB_PATH, help=f"数据库文件，默认 {DB_PATH}")
    p.add_argument("--days", type=int, default=None,
                   help="临时覆盖 Settings 表的 Days 项；不传则读 Settings")
    p.add_argument("--date", default=None, help="只取某一天，如 20241101")
    p.add_argument("--start", default=None, help="区间开始日")
    p.add_argument("--end", default=None, help="区间结束日（也用作天数的截止日）")
    p.add_argument("--token", default=None, help="Tushare token")
    p.add_argument("--allow-partial", action="store_true",
                   help="数据明显不全时仍然写入（默认不写）")
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

        # -------- 完整性校验（数据没更新完就不写库） --------
        if not check_completeness(conn, raw, args.allow_partial):
            return 2

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

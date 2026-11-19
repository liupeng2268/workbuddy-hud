# -*- coding: utf-8 -*-
"""ZCode 数据层回归用例。

全部在系统临时目录内造 sqlite 数据，只读测试，不触碰用户真实 ZCode 库。
覆盖：命中率口径、10 分钟活跃窗口、按活跃时间倒序、空记录跳过、
纯数字标题回退、速率窗口、MaxSessions 截断、db 缺失容错。
"""

import os
import sys
import tempfile
import sqlite3
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "最终成果"))

import zcode_reader as zr

PASS = 0
FAIL = 0


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print("[PASS] %s %s" % (name, detail))
    else:
        FAIL += 1
        print("[FAIL] %s %s" % (name, detail))


def make_db(path, rows):
    """建一个最小可用的 model_usage + session 库。rows 为 dict 列表。"""
    con = sqlite3.connect(path)
    cur = con.cursor()
    cur.execute("CREATE TABLE session (id TEXT PRIMARY KEY, title TEXT)")
    cur.execute(
        "CREATE TABLE model_usage (session_id TEXT, input_tokens INTEGER, "
        "output_tokens INTEGER, cache_read_input_tokens INTEGER, "
        "cache_creation_input_tokens INTEGER, started_at INTEGER, "
        "completed_at INTEGER, model_id TEXT)"
    )
    for r in rows:
        cur.execute(
            "INSERT OR IGNORE INTO session VALUES (?, ?)",
            (r["sid"], r.get("title", "")),
        )
        # completed_at 默认等于 started_at；长调用用例可显式指定
        cur.execute(
            "INSERT INTO model_usage VALUES (?,?,?,?,?,?,?,?)",
            (
                r["sid"], r["inp"], r["out"], r["cr"], r["cw"],
                r["ts"], r.get("done", r["ts"]),
                r.get("model", "GLM-5.3-Flash"),
            ),
        )
    con.commit()
    con.close()


def make_reader(db_path):
    """构造 reader 但不启动线程，直接驱动 _scan 保证测试确定性。"""
    obj = zr.ZcodeReader.__new__(zr.ZcodeReader)
    obj.db_path = db_path
    import threading
    obj._lock = threading.Lock()
    obj._sessions = {}
    obj._error = None
    import threading as _t
    obj._primed = _t.Event()
    return obj


def main():
    print("=== ZCode 数据层回归 ===\n")
    now_ms = int(time.time() * 1000)
    tmp = tempfile.mkdtemp(prefix="zcode_verify_")

    # --- 1. 口径：cacheRead/input（ZCode 源码口径，P19）---
    # inputTokens 已包含 cacheRead，cr=100, input=900 -> 100/900=0.1111
    s = zr._ZSession("sess_a", "测试会话", "GLM")
    s.add(now_ms - 5000, 900, 100, 100, 0)
    row = s.row(now_ms, now_ms - zr.SPEED_WINDOW_MS, now_ms - zr.SPEED_KEEP_MS)
    check("口径 cr/input=100/900=0.1111", abs(row["hit_rate"] - 100 / 900.0) < 1e-9,
          "实际=%s" % row["hit_rate"])
    check("标题带 ZCode 前缀", row["title"].startswith("ZCode · "))
    # 回归：高命中率场景必须 >90%，不能被旧错误口径腰斩到 ~50%（P19）
    s_hi = zr._ZSession("sess_hi", "高命中", "GLM")
    s_hi.add(now_ms - 5000, 10000, 100, 9800, 0)   # cr=9800, input=10000 -> 98%
    row_hi = s_hi.row(now_ms, now_ms - zr.SPEED_WINDOW_MS, now_ms - zr.SPEED_KEEP_MS)
    check("高命中率不被腰斩（cr/input=0.98，旧口径会得 0.495）",
          abs(row_hi["hit_rate"] - 0.98) < 1e-9,
          "实际=%s" % row_hi["hit_rate"])

    # --- 2. 纯数字标题回退 ---
    s2 = zr._ZSession("sess_abcdef123456", "6", "GLM")
    s2.add(now_ms - 5000, 10, 10, 0, 0)
    row2 = s2.row(now_ms, now_ms - zr.SPEED_WINDOW_MS, now_ms - zr.SPEED_KEEP_MS)
    check("纯数字标题回退到 sessionId 尾8位",
          "ef123456" in row2["title"], "实际=%s" % row2["title"])

    # --- 3. 活跃窗口 + 倒序 + 空记录跳过 ---
    rows = [
        # 活跃会话 A：10 分钟内有写入；cr=50,input=100 -> 命中率 50%
        dict(sid="sess_A", title="会话A", inp=100, out=50, cr=50, cw=0,
             ts=now_ms - 60000),
        # 活跃会话 B：最近写入；cr=90,input=100 -> 命中率 90%
        dict(sid="sess_B", title="会话B", inp=100, out=20, cr=90, cw=0,
             ts=now_ms - 10000),
        # 过期会话 C：20 分钟前，应被排除
        dict(sid="sess_C", title="会话C", inp=1, out=1, cr=1, cw=0,
             ts=now_ms - 20 * 60 * 1000),
        # 空记录：全 0，应被跳过（不产生会话）
        dict(sid="sess_empty", title="空", inp=0, out=0, cr=0, cw=0,
             ts=now_ms - 1000),
    ]
    db = os.path.join(tmp, "test.sqlite")
    make_db(db, rows)
    reader = make_reader(db)
    reader._scan()
    snap = reader.snapshot()

    check("活跃会话数=2（排除过期与空记录）", snap["zcode_count"] == 2,
          "实际=%s" % snap["zcode_count"])
    check("按活跃时间倒序（B 在前）",
          snap["zcode_sessions"][0]["session_id"] == "sess_B",
          "实际=%s" % snap["zcode_sessions"][0]["session_id"])
    check("会话B 命中率=90/100=0.9",
          abs(snap["zcode_sessions"][0]["hit_rate"] - 0.9) < 1e-9,
          "实际=%s" % snap["zcode_sessions"][0]["hit_rate"])
    check("真实中文标题保留",
          "会话B" in snap["zcode_sessions"][0]["title"],
          "实际=%s" % snap["zcode_sessions"][0]["title"])

    # --- 4. 多记录累计 ---
    rows2 = [
        dict(sid="sess_D", title="累计", inp=100, out=10, cr=50, cw=0,
             ts=now_ms - 5000),
        dict(sid="sess_D", title="累计", inp=100, out=10, cr=50, cw=0,
             ts=now_ms - 3000),
    ]
    db2 = os.path.join(tmp, "test2.sqlite")
    make_db(db2, rows2)
    reader2 = make_reader(db2)
    reader2._scan()
    snap2 = reader2.snapshot()
    d = snap2["zcode_sessions"][0]
    check("同会话多条累计 records=2", d["records"] == 2,
          "实际=%s" % d["records"])
    check("累计命中率=100/200=0.5",
          abs(d["hit_rate"] - 0.5) < 1e-9,
          "实际=%s" % d["hit_rate"])
    check("累计输出=20", d["total_out"] == 20, "实际=%s" % d["total_out"])

    # --- 5. 速率窗口 ---
    s3 = zr._ZSession("sess_speed", "速率", "GLM")
    s3.add(now_ms - 30000, 0, 3000, 0, 0)   # 30 秒前输出 3000
    s3.add(now_ms - 10000, 0, 1000, 0, 0)   # 10 秒前输出 1000
    row3 = s3.row(now_ms, now_ms - zr.SPEED_WINDOW_MS, now_ms - zr.SPEED_KEEP_MS)
    check("速率窗口内累计输出=4000",
          abs(sum(s[1] for s in s3.samples) - 4000) < 1e-9)
    check("速率非空闲（10 秒内有样本）", not row3["speed_idle"])

    # --- 5b. 长调用速率必须用 completed_at 而非 started_at（P16） ---
    s3b = zr._ZSession("sess_long", "长调用", "GLM")
    # 调用 5 分钟前开始、10 秒前完成，输出 14002 tokens
    s3b.add(now_ms - 300000, 100, 14002, 100, 0, done_ts=now_ms - 10000)
    row3b = s3b.row(now_ms, now_ms - zr.SPEED_WINDOW_MS, now_ms - zr.SPEED_KEEP_MS)
    check("长调用按 completed_at 计入速率窗口（非 started_at）",
          row3b["speed"] is not None,
          "speed=%s" % row3b["speed"])
    check("长调用速率非空闲", not row3b["speed_idle"])
    # 用调用自身时长作分母：14002/(300s) ≈ 46.7，不应虚高到 1400
    check("长调用速率按真实时长算（无单样本虚高）",
          row3b["speed"] is not None and 40 < row3b["speed"] < 55,
          "speed=%.1f" % row3b["speed"] if row3b["speed"] else "speed=None")
    check("长调用 active_ts 取 completed_at（max_ts 接近 now）",
          now_ms - s3b.max_ts < 20000, "距今=%dms" % (now_ms - s3b.max_ts))
    # 反向验证：started_at 远早于窗口、completed_at 在窗口内 ->
    # 若错误地用 started_at，样本应在窗口外，速率为 None
    s3c = zr._ZSession("sess_long2", "长调用2", "GLM")
    s3c.add(now_ms - 15 * 60 * 1000, 100, 14002, 100, 0,
            done_ts=now_ms - 10000)   # 15 分钟前开始、10 秒前完成
    row3c = s3c.row(now_ms, now_ms - zr.SPEED_WINDOW_MS, now_ms - zr.SPEED_KEEP_MS)
    check("对照：样本按 completed_at 落入窗口（started_at 早已过期）",
          row3c["speed"] is not None,
          "speed=%.1f" % row3c["speed"] if row3c["speed"] else "speed=None")

    # --- 6. MaxSessions 截断 ---
    many = [
        dict(sid="sess_%d" % i, title="会话%d" % i, inp=10, out=10,
             cr=10, cw=0, ts=now_ms - i * 1000)
        for i in range(6)
    ]
    db3 = os.path.join(tmp, "test3.sqlite")
    make_db(db3, many)
    reader3 = make_reader(db3)
    reader3._scan()
    snap3 = reader3.snapshot()
    check("zcode_count=6（真实总数）", snap3["zcode_count"] == 6)
    check("zcode_sessions 输出全部活跃会话（上限 MAX_SESSIONS=%d）" % zr.MAX_SESSIONS,
          len(snap3["zcode_sessions"]) == min(6, zr.MAX_SESSIONS))

    # --- 7. db 缺失容错 ---
    reader4 = make_reader(os.path.join(tmp, "nonexistent.sqlite"))
    reader4._scan()
    check("db 缺失时 error 非空", bool(reader4._error),
          "实际=%s" % reader4._error)

    print("\n" + "-" * 60)
    print("汇总：共 %d 项断言，通过 %d，失败 %d" % (PASS + FAIL, PASS, FAIL))
    print("结论：%s" % ("PASS" if FAIL == 0 else "FAIL"))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

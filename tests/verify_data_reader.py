# -*- coding: utf-8 -*-
"""UsageReader 的回归验收脚本（纯标准库，无需 pytest）。

覆盖：基线口径、增量追加、半行补完、subagents 计入、历史不重复、
截断重写不二次计数、运行期新增文件的历史口径、速率空闲归零。
全部用例只在 tempfile 临时目录里造数据，绝不读写用户真实日志。

用法（在仓库根目录执行）：
    python tests/verify_data_reader.py

退出码：全部通过 0，有失败 1。

历史：本脚本最初为验证 ADR-0001 的两条缺陷而写——增量读取每批丢首条、
历史口径被会话扫描重复计数。改动 `data_reader.py` 的读取或记账逻辑后请重跑。
"""

import json
import os
import shutil
import sys
import tempfile
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.abspath(os.path.join(_HERE, "..", "最终成果"))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

try:
    import data_reader
    from data_reader import UsageReader
except Exception as _exc:  # 导入失败也要给出明确结论
    print("[FAIL] 无法导入 data_reader.UsageReader（%s 起）：%s: %s"
          % (_SRC, type(_exc).__name__, _exc))
    sys.exit(1)


# ---- 结果收集 -------------------------------------------------------------

_RESULTS = []   # (case_id, desc, ok, actual, expected)


def _record(case_id, desc, actual, expected):
    ok = actual == expected
    _RESULTS.append((case_id, desc, ok, actual, expected))
    if ok:
        print("[PASS] 用例%s %s | 实际=%r" % (case_id, desc, actual))
    else:
        print("[FAIL] 用例%s %s | 实际=%r 期望=%r"
              % (case_id, desc, actual, expected))
    sys.stdout.flush()
    return ok


def _record_true(case_id, desc, cond, detail=""):
    ok = bool(cond)
    _RESULTS.append((case_id, desc, ok, detail or cond, True))
    if ok:
        print("[PASS] 用例%s %s | %s" % (case_id, desc, detail))
    else:
        print("[FAIL] 用例%s %s | 实际=%r 期望=%r"
              % (case_id, desc, detail, True))
    sys.stdout.flush()
    return ok


# ---- 造数据工具 -----------------------------------------------------------

def _mk_case_dir():
    """新建一个独立临时根目录 + 项目子目录。"""
    root = tempfile.mkdtemp(prefix="vrif_")
    proj = os.path.join(root, "proj")
    os.makedirs(proj)
    return root, proj


def _line(ts, hit=900, miss=100, prompt=1000, completion=50, model="m"):
    """生成一行合法日志（含 rawUsage 子串）。"""
    obj = {
        "timestamp": ts,
        "providerData": {
            "model": model,
            "rawUsage": {
                "prompt_cache_hit_tokens": hit,
                "prompt_cache_miss_tokens": miss,
                "prompt_tokens": prompt,
                "completion_tokens": completion,
            },
        },
    }
    return json.dumps(obj, ensure_ascii=False) + "\n"


def _stamps(count, step_ms=2000):
    """生成 count 个递增时间戳，全部落在速率窗口（60s）内。"""
    end = int(time.time() * 1000) - 1000
    return [end - (count - 1 - i) * step_ms for i in range(count)]


def _append(path, text):
    """二进制追加并落盘，避免 Windows 文本模式换行转换与缓冲延迟。"""
    with open(path, "ab") as handle:
        handle.write(text.encode("utf-8"))
        handle.flush()
        os.fsync(handle.fileno())


def _append_records(path, count, start_ts=None):
    stamps = _stamps(count) if start_ts is None else [
        start_ts + i * 2000 for i in range(count)
    ]
    _append(path, "".join(_line(ts) for ts in stamps))
    return stamps


def _wait(reader, predicate, timeout):
    """轮询 snapshot 直到 predicate 为真或超时，返回最后一次快照。"""
    deadline = time.time() + timeout
    snap = reader.snapshot()
    while True:
        if predicate(snap):
            return snap
        if time.time() >= deadline:
            return snap
        time.sleep(0.1)
        snap = reader.snapshot()


def _wait_history(reader, timeout=12.0):
    """等历史口径首次全量完成（history_ready），再留一点稳定时间。"""
    snap = _wait(reader, lambda s: s.get("history_ready") is True, timeout)
    time.sleep(0.5)
    return reader.snapshot()


def _files_of(snap):
    """兼容 files / file_count 两种字段名。"""
    if "files" in snap:
        return snap.get("files")
    return snap.get("file_count")


def _patch_const(name, value):
    """临时改写 data_reader 的模块级常量，返回原值现场供 _restore_const 还原。"""
    saved = (hasattr(data_reader, name), getattr(data_reader, name, None))
    setattr(data_reader, name, value)
    return saved


def _restore_const(name, saved):
    """还原 _patch_const 的现场；原本不存在的常量直接删除。"""
    had, old = saved
    if had:
        setattr(data_reader, name, old)
    elif hasattr(data_reader, name):
        delattr(data_reader, name)


# ---- 用例 1~3：基线 + 追加 1 条 + 追加 2 条 --------------------------------

def case_baseline_and_append():
    case = "1/2/3"
    root, proj = _mk_case_dir()
    try:
        main = os.path.join(proj, "sess-a.jsonl")
        _append_records(main, 10)

        reader = UsageReader(root)

        # --- 用例 1：基线 ---
        snap = _wait_history(reader)
        _record(case, "基线 records==10", snap.get("records"), 10)
        _record(case, "基线 history_records==10（修复前会是 20）",
                snap.get("history_records"), 10)

        # --- 用例 2：追加 1 条 ---
        _append_records(main, 1, start_ts=int(time.time() * 1000))
        snap = _wait(reader, lambda s: (s.get("records") or 0) >= 11, 2.0)
        time.sleep(0.6)
        snap = reader.snapshot()
        _record(case, "追加 1 条后 records==11（修复前仍是 10）",
                snap.get("records"), 11)

        # --- 用例 3：再追加 2 条 ---
        _append_records(main, 2, start_ts=int(time.time() * 1000) + 2000)
        snap = _wait(reader, lambda s: (s.get("records") or 0) >= 13, 2.0)
        time.sleep(0.6)
        snap = reader.snapshot()
        _record(case, "再追加 2 条后 records==13", snap.get("records"), 13)
        _record_true(case, "追加过程 error 为空", snap.get("error") is None,
                     snap.get("error"))
    except Exception as exc:
        _record_true(case, "用例未抛异常", False, "%s: %s"
                     % (type(exc).__name__, exc))
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---- 用例 4：半行补完 -----------------------------------------------------

def case_half_line():
    case = "4"
    root, proj = _mk_case_dir()
    try:
        main = os.path.join(proj, "sess-h.jsonl")
        _append_records(main, 2)

        reader = UsageReader(root)
        snap = _wait(reader, lambda s: s.get("records") == 2, 6.0)
        base = snap.get("records")
        _record(case, "半行前置基线 records==2", base, 2)

        full = _line(int(time.time() * 1000))
        cut = len(full) // 2
        half, rest = full[:cut], full[cut:]

        _append(main, half)                  # 没有换行符的半条
        time.sleep(1.5)
        _append(main, rest)                  # 剩余部分 + 换行
        time.sleep(2.0)

        snap = reader.snapshot()
        _record(case, "半行补完后 records==基线+1", snap.get("records"),
                (base or 0) + 1)
        _record_true(case, "半行补完无异常", snap.get("error") is None,
                     snap.get("error"))
    except Exception as exc:
        _record_true(case, "用例未抛异常", False, "%s: %s"
                     % (type(exc).__name__, exc))
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---- 用例 5：subagents 计入会话口径 --------------------------------------

def case_subagents():
    case = "5"
    root, proj = _mk_case_dir()
    try:
        main = os.path.join(proj, "sess-s.jsonl")
        _append_records(main, 3)

        sub_dir = os.path.join(proj, "sess-s", "subagents")
        os.makedirs(sub_dir)
        sub = os.path.join(sub_dir, "agent-x.jsonl")
        _append_records(sub, 2)

        # 主日志保持最新 mtime，确保被选为当前会话
        now = time.time()
        os.utime(sub, (now - 120, now - 120))
        os.utime(main, (now, now))

        reader = UsageReader(root)
        snap = _wait(reader, lambda s: s.get("records") == 5, 6.0)
        _record(case, "主日志 3 条 + subagents 2 条 → records==5",
                snap.get("records"), 5)
        _record_true(case, "active_file 为当前主日志",
                     snap.get("active_file") == "sess-s.jsonl",
                     snap.get("active_file"))
        _record_true(case, "subagents 场景 error 为空",
                     snap.get("error") is None, snap.get("error"))
    except Exception as exc:
        _record_true(case, "用例未抛异常", False, "%s: %s"
                     % (type(exc).__name__, exc))
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---- 用例 6：历史不重复（旧会话 + 当前会话） ------------------------------

def case_history_no_duplicate():
    case = "6"
    root, proj = _mk_case_dir()
    try:
        old = os.path.join(proj, "old.jsonl")
        _append_records(old, 5)
        now = time.time()
        os.utime(old, (now - 3600, now - 3600))   # 旧会话：mtime 明显更早

        cur = os.path.join(proj, "cur.jsonl")
        _append_records(cur, 10)
        os.utime(cur, (now, now))                 # 当前会话：mtime 最新

        reader = UsageReader(root)
        snap = _wait_history(reader)
        _record(case, "会话口径只统计当前会话 records==10",
                snap.get("records"), 10)
        _record(case, "基线历史 == 15（会话扫描不重复计入历史）",
                snap.get("history_records"), 15)
        _record_true(case, "历史命中率 history_hit_rate 非空",
                     isinstance(snap.get("history_hit_rate"), float),
                     snap.get("history_hit_rate"))
        _record_true(case, "旧会话场景 error 为空",
                     snap.get("error") is None, snap.get("error"))
    except Exception as exc:
        _record_true(case, "用例未抛异常", False, "%s: %s"
                     % (type(exc).__name__, exc))
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---- 用例 7：文件被截断后重写，不得二次计数 --------------------------------

def case_truncate_rewrite():
    case = "7"
    root, proj = _mk_case_dir()
    try:
        main = os.path.join(proj, "sess-t.jsonl")
        _append_records(main, 10)

        reader = UsageReader(root)
        snap = _wait_history(reader)
        _record(case, "截断前基线 records==10", snap.get("records"), 10)
        _record(case, "截断前基线 history_records==10",
                snap.get("history_records"), 10)

        # 清空重写 3 条：size 变小，程序应按截断处理并重算，而不是累加
        stamps = _stamps(3)
        with open(main, "wb") as handle:
            handle.write("".join(_line(ts) for ts in stamps).encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())

        snap = _wait(reader, lambda s: s.get("records") == 3, 8.0)
        _record(case, "截断重写后 records==3（修复前会是 13）",
                snap.get("records"), 3)

        snap = _wait(
            reader, lambda s: s.get("history_records") == 3, 15.0
        )
        _record(case, "截断重写后 history_records==3（修复前会是 13）",
                snap.get("history_records"), 3)
        _record_true(case, "截断重写场景 error 为空", snap.get("error") is None,
                     snap.get("error"))
    except Exception as exc:
        _record_true(case, "用例未抛异常", False, "%s: %s"
                     % (type(exc).__name__, exc))
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---- 用例 8：运行期新增的大文件，历史口径要读头部 --------------------------

def case_new_big_file_history():
    case = "8"
    root, proj = _mk_case_dir()
    saved_tail = None
    try:
        main = os.path.join(proj, "cur.jsonl")
        _append_records(main, 1)

        reader = UsageReader(root)
        snap = _wait_history(reader)
        _record(case, "占位主日志 records==1", snap.get("records"), 1)
        _record(case, "占位主日志 history_records==1",
                snap.get("history_records"), 1)

        # 把「新文件只读尾部」的预算压到 1KB，让截断行为在 2000 条上可见
        saved_tail = _patch_const("NEW_FILE_TAIL_BYTES", 1024)

        # 大文件先写在扫描目录之外，落盘、改旧 mtime 后再搬进来，
        # 避免「刚创建时 mtime 最新」的瞬间抢走当前会话
        staging = os.path.join(root, "staging")
        os.makedirs(staging)
        staged = os.path.join(staging, "old-big.jsonl")
        _append_records(staged, 2000)
        old = time.time() - 3600
        os.utime(staged, (old, old))
        os.replace(staged, os.path.join(proj, "old-big.jsonl"))

        snap = _wait(
            reader, lambda s: s.get("history_records") == 2001, 25.0
        )
        _record(case, "历史口径读全新增大文件：history_records==2001"
                "（修复前只算尾部几条）", snap.get("history_records"), 2001)
        _record(case, "旧 mtime 大文件未抢走当前会话：records==1",
                snap.get("records"), 1)
        _record_true(case, "新增大文件场景 error 为空",
                     snap.get("error") is None, snap.get("error"))
    except Exception as exc:
        _record_true(case, "用例未抛异常", False, "%s: %s"
                     % (type(exc).__name__, exc))
    finally:
        if saved_tail is not None:
            _restore_const("NEW_FILE_TAIL_BYTES", saved_tail)
        shutil.rmtree(root, ignore_errors=True)


# ---- 用例 9：停止生成后速率归零，单条样本要给出数值 ------------------------

def case_speed_single_sample_and_idle():
    case = "9"
    root, proj = _mk_case_dir()
    saved_idle = None
    try:
        main = os.path.join(proj, "sess-v.jsonl")
        _append_records(main, 1)

        reader = UsageReader(root)
        snap = _wait(reader, lambda s: (s.get("records") or 0) >= 1, 8.0)
        _record(case, "速率用例基线 records==1", snap.get("records"), 1)

        speed = snap.get("speed")
        _record_true(
            case, "单条样本 speed 为数值且 > 0（修复前为 None）",
            isinstance(speed, (int, float))
            and not isinstance(speed, bool)
            and speed > 0,
            "speed=%r" % (speed,),
        )

        # 空闲阈值压到 300ms，1 秒后就必然超时
        saved_idle = _patch_const("SPEED_IDLE_MS", 300)
        time.sleep(1.2)
        snap = reader.snapshot()
        _record_true(case, "空闲超时后 speed 为 None（不残留旧速率）",
                     snap.get("speed") is None, "speed=%r" % (snap.get("speed"),))
        _record_true(case, "空闲超时后 speed_idle 为 True",
                     snap.get("speed_idle") is True,
                     "speed_idle=%r" % (snap.get("speed_idle"),))
        _record_true(case, "速率用例 error 为空", snap.get("error") is None,
                     snap.get("error"))
    except Exception as exc:
        _record_true(case, "用例未抛异常", False, "%s: %s"
                     % (type(exc).__name__, exc))
    finally:
        if saved_idle is not None:
            _restore_const("SPEED_IDLE_MS", saved_idle)
        shutil.rmtree(root, ignore_errors=True)


# ---- 主流程 ---------------------------------------------------------------

def main():
    started = time.time()
    print("验收目标：%s" % os.path.join(_SRC, "data_reader.py"))
    print("临时目录前缀：vrif_（系统 temp 下，结束即删）")
    print("-" * 68)

    case_baseline_and_append()
    case_half_line()
    case_subagents()
    case_history_no_duplicate()
    case_truncate_rewrite()
    case_new_big_file_history()
    case_speed_single_sample_and_idle()

    print("-" * 68)
    failed = [item for item in _RESULTS if not item[2]]
    total = len(_RESULTS)
    print("汇总：共 %d 项断言，通过 %d，失败 %d，耗时 %.1f 秒"
          % (total, total - len(failed), len(failed), time.time() - started))
    if failed:
        for case_id, desc, _ok, actual, expected in failed:
            print("  失败 → 用例%s %s：实际=%r 期望=%r"
                  % (case_id, desc, actual, expected))
        print("结论：FAIL")
        sys.exit(1)
    print("结论：PASS")


if __name__ == "__main__":
    main()

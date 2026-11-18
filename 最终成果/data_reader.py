# -*- coding: utf-8 -*-
"""WorkBuddy 悬浮窗 —— 数据层（双口径版）。

只读扫描 ``~/.workbuddy/projects`` 下的 ``*.jsonl`` 会话日志，同时维护两套
互相独立的统计：

  * **会话口径**：当前活跃会话（mtime 最新的主日志 + 其 subagents）
  * **历史口径**：全部日志的全历史累计

理由：会话口径反映「这次对话的缓存效率」，数值会明显波动；历史口径反映
「长期平均值」，稳定在 96% 上下。两者各有用途，由界面按用户设置选用。

加载策略：
  * 会话口径首次全量（通常 <1 秒），之后按 (size, offset) 增量
  * 历史口径首次全量（324 文件 / 354 MB 约 2~3 秒），之后每 4 个周期增量一次
  * 两套统计各有独立的 offset 表，互不干扰
  * 会话切换时只重建会话口径，历史口径保持连续

任何异常都写入 error 字段，绝不向调用方抛出。
"""

import json
import os
import threading
import time
from collections import deque

# ---- 可调常量 -------------------------------------------------------------

#: 增量扫描的基础间隔（秒）
SCAN_INTERVAL = 0.8

#: 会话口径增量预算
SESSION_TIME_BUDGET = 0.15
SESSION_MAX_BYTES = 8 * 1024 * 1024

#: 历史口径增量预算（每 HISTORY_INTERVAL 个周期执行一次）
HISTORY_INTERVAL = 4
HISTORY_TIME_BUDGET = 0.35
HISTORY_MAX_BYTES = 16 * 1024 * 1024

#: 增量阶段新出现的文件，最多回溯读取末尾这么多字节
NEW_FILE_TAIL_BYTES = 4 * 1024 * 1024

#: 速率的滑动窗口（毫秒）
SPEED_WINDOW_MS = 60 * 1000

#: 样本保留时长（毫秒），比窗口更长，便于平滑过期判定
SPEED_KEEP_MS = 180 * 1000

#: 首调等待时间：0 表示不等待，后台扫描期间由界面显示占位
PRIME_WAIT = 0.0


def _to_int(value):
    """把日志里的数值字段安全转成 int，失败返回 0。"""
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0
    return 0


def _to_ms(value):
    """时间戳归一化为毫秒整数。日志为毫秒 epoch，容错秒级与字符串形态。"""
    if isinstance(value, bool) or value is None:
        return 0
    if isinstance(value, (int, float)):
        ts = int(value)
    elif isinstance(value, str):
        try:
            ts = int(float(value))
        except (TypeError, ValueError):
            return 0
    else:
        return 0
    if ts <= 0:
        return 0
    if ts < 100000000000:      # 10 位秒级时间戳换算为毫秒
        ts *= 1000
    return ts


def _ratio(hit, miss, prompt, cached):
    """命中率：主口径 hit/(hit+miss)，无分母时回退 cached/prompt。"""
    denominator = hit + miss
    if denominator > 0:
        value = hit / float(denominator)
    elif prompt > 0:
        value = cached / float(prompt)
    else:
        return None
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


class UsageReader:
    """读取 WorkBuddy 会话日志，同时输出会话与历史两套指标。"""

    def __init__(self, projects_dir):
        self.projects_dir = projects_dir

        # 锁序固定为 _scan_lock -> _state_lock
        self._scan_lock = threading.Lock()
        self._state_lock = threading.Lock()

        # ---- 会话口径 ----
        self._files = {}              # 会话相关文件 -> {"offset", "size"}
        self._current_session = None  # 当前会话主日志绝对路径
        self._s_hit = 0
        self._s_miss = 0
        self._s_prompt = 0
        self._s_cached = 0
        self._s_out = 0
        self._s_records = 0
        self._s_min_ts = 0            # 会话内最早记录时间（毫秒）
        self._s_max_ts = 0            # 会话内最新记录时间（毫秒）
        self._s_last_prompt = 0       # 最新一条请求的 prompt_tokens
        self._s_model = ""            # 当前会话模型
        self._samples = deque()       # (timestamp_ms, completion_tokens)

        # ---- 历史口径 ----
        self._h_files = {}
        self._h_hit = 0
        self._h_miss = 0
        self._h_prompt = 0
        self._h_cached = 0
        self._h_out = 0
        self._h_records = 0
        self._h_ready = False

        # ---- 通用 ----
        self._file_count = 0
        self._active_file = ""
        self._error = None
        self._primed = threading.Event()
        self._tick = 0

        self._worker = threading.Thread(
            target=self._run, name="usage-reader", daemon=True
        )
        self._worker.start()

    # -- 对外接口 -----------------------------------------------------------

    def snapshot(self):
        """返回指标快照 dict。任何异常都被吞掉并写入 error 字段。"""
        if not self._primed.is_set():
            self._primed.wait(PRIME_WAIT)

        now = time.time()
        with self._state_lock:
            result = self._build(now)
            result["error"] = self._error
        return result

    # -- 后台调度 -----------------------------------------------------------

    def _run(self):
        # 1) 会话口径首次全量：只读当前会话，通常 1 秒内完成
        try:
            self._scan_session(full=True)
        except Exception as exc:
            self._set_error(exc)
        finally:
            self._primed.set()

        # 2) 历史口径全量：后台补齐全部历史，约 2~3 秒
        try:
            self._scan_history(full=True)
            with self._state_lock:
                self._h_ready = True
        except Exception as exc:
            self._set_error(exc)

        # 3) 之后固定节奏增量
        while True:
            time.sleep(SCAN_INTERVAL)
            self._tick += 1
            try:
                self._scan_session(full=False)
            except Exception as exc:
                self._set_error(exc)
            if self._tick % HISTORY_INTERVAL == 0:
                try:
                    self._scan_history(full=False)
                except Exception as exc:
                    self._set_error(exc)

    def _set_error(self, exc):
        with self._state_lock:
            self._error = "%s: %s" % (type(exc).__name__, exc)

    # -- 会话口径扫描 -------------------------------------------------------

    def _scan_session(self, full):
        root = self.projects_dir
        if not os.path.isdir(root):
            raise IOError("日志目录不存在: %s" % root)

        entries = self._collect(root)
        current, related = self._pick_current_session(entries)
        related.sort(key=lambda item: item[0], reverse=True)

        with self._scan_lock:
            switched = current != self._current_session
            if switched:
                self._current_session = current
                self._reset_session()
            do_full = switched or full

            with self._state_lock:
                self._file_count = len(related)
                self._active_file = os.path.basename(current) if current else ""

            alive = set(item[1] for item in related)
            for path in [p for p in self._files if p not in alive]:
                del self._files[path]

            budget = None if do_full else SESSION_MAX_BYTES
            deadline = (
                None if do_full else time.perf_counter() + SESSION_TIME_BUDGET
            )
            for _mtime, path, size in related:
                if deadline is not None and time.perf_counter() >= deadline:
                    break
                if budget is not None and budget <= 0:
                    break
                try:
                    used = self._consume(
                        path, size, budget, do_full, self._files, True, False
                    )
                except OSError:
                    continue
                if budget is not None:
                    budget -= used

        with self._state_lock:
            self._error = None

    def _reset_session(self):
        """会话切换时清零会话口径，历史口径保持不变。"""
        with self._state_lock:
            self._s_hit = 0
            self._s_miss = 0
            self._s_prompt = 0
            self._s_cached = 0
            self._s_out = 0
            self._s_records = 0
            self._s_min_ts = 0
            self._s_max_ts = 0
            self._s_last_prompt = 0
            self._s_model = ""
            self._samples.clear()
        self._files.clear()

    @staticmethod
    def _pick_current_session(entries):
        """挑出当前活跃会话：mtime 最新的主日志 + 它名下的 subagents。"""
        marker = os.sep + "subagents" + os.sep
        mains = [item for item in entries if marker not in item[1]]
        if not mains:
            return None, []
        mains.sort(key=lambda item: item[0], reverse=True)
        current = mains[0][1]
        session_dir = os.path.splitext(current)[0]
        sub_prefix = os.path.join(session_dir, "subagents") + os.sep
        related = [
            item for item in entries
            if item[1] == current or item[1].startswith(sub_prefix)
        ]
        return current, related

    # -- 历史口径扫描 -------------------------------------------------------

    def _scan_history(self, full):
        root = self.projects_dir
        if not os.path.isdir(root):
            raise IOError("日志目录不存在: %s" % root)

        entries = self._collect(root)
        entries.sort(key=lambda item: item[0], reverse=True)

        with self._scan_lock:
            alive = set(item[1] for item in entries)
            for path in [p for p in self._h_files if p not in alive]:
                del self._h_files[path]

            budget = None if full else HISTORY_MAX_BYTES
            deadline = (
                None if full else time.perf_counter() + HISTORY_TIME_BUDGET
            )
            for _mtime, path, size in entries:
                if deadline is not None and time.perf_counter() >= deadline:
                    break
                if budget is not None and budget <= 0:
                    break
                try:
                    used = self._consume(
                        path, size, budget, full, self._h_files, False
                    )
                except OSError:
                    continue
                if budget is not None:
                    budget -= used

    # -- 通用读取与解析 -----------------------------------------------------

    def _consume(self, path, size, budget, full, files_map, to_session,
                 to_history=True):
        """读取单个文件的新增字节，返回本轮消耗的字节数。

        files_map 决定 offset 归属（会话表或历史表），因此同一文件可以被
        两套统计各自读取一次，互不影响计数。to_history 为 False 时本批数据
        不计入历史口径（会话扫描只喂会话计数，历史计数只由历史扫描写入）。

        state 里的 boundary 记录 offset 是否正好落在行首：True 表示下一批
        可以直接解析，False 表示 offset 落在行中（只出现在新文件回溯读取）。
        """
        state = files_map.get(path)
        first = state is None

        if first:
            start = 0 if full else max(0, size - NEW_FILE_TAIL_BYTES)
            boundary = start == 0      # 从 0 开始必为行首
        else:
            start = state["offset"]
            boundary = state.get("boundary", True)   # 旧结构按行首处理
            if size < start:          # 文件被截断，从头重读
                start = 0
                boundary = True

        if size <= start:
            files_map[path] = {
                "offset": start, "size": size, "boundary": boundary
            }
            return 0

        want = size - start
        if budget is not None and want > budget:
            want = budget

        with open(path, "rb") as handle:
            handle.seek(start)
            data = handle.read(want)
        if not data:
            files_map[path] = {
                "offset": start, "size": size, "boundary": boundary
            }
            return 0

        base = start
        payload = data

        # 只有在 offset 不在行首时，首行才是半行，丢弃到第一个换行为止
        if not boundary and start > 0:
            newline = payload.find(b"\n")
            if newline < 0:
                files_map[path] = {
                    "offset": start, "size": size, "boundary": boundary
                }
                return len(data)
            base = start + newline + 1
            payload = payload[newline + 1:]

        # 只处理完整行，尾部残行留到下次，避免半截 JSON 与重复计数
        last_newline = payload.rfind(b"\n")
        if last_newline < 0:
            # 一条完整行都没有：offset 原地不动，等这条行写完整再解析
            files_map[path] = {
                "offset": start, "size": size, "boundary": boundary
            }
            return len(data)

        self._parse(payload[:last_newline + 1], to_session, to_history)
        files_map[path] = {
            "offset": base + last_newline + 1, "size": size, "boundary": True
        }
        return want

    def _parse(self, blob, to_session, to_history=True):
        """解析一批完整行。会话口径按 to_session 累加，历史口径按 to_history 累加。"""
        keep_after = time.time() * 1000.0 - SPEED_KEEP_MS
        hit = miss = prompt = cached = out = records = 0
        samples = []
        min_ts = 0
        max_ts = 0
        last_prompt = 0
        model = ""

        for line in blob.split(b"\n"):
            if not line or b'"rawUsage"' not in line:
                continue
            try:
                record = json.loads(line.decode("utf-8", "replace"))
            except Exception:
                continue
            if not isinstance(record, dict):
                continue

            usage = None
            provider = record.get("providerData")
            if isinstance(provider, dict):
                candidate = provider.get("rawUsage")
                if isinstance(candidate, dict):
                    usage = candidate
            if usage is None:
                candidate = record.get("rawUsage")
                if isinstance(candidate, dict):
                    usage = candidate
            if usage is None:
                continue

            hit += _to_int(usage.get("prompt_cache_hit_tokens"))
            miss += _to_int(usage.get("prompt_cache_miss_tokens"))
            prompt_tokens = _to_int(usage.get("prompt_tokens"))
            prompt += prompt_tokens
            completion = _to_int(usage.get("completion_tokens"))
            out += completion
            records += 1

            detail = 0
            details = usage.get("prompt_tokens_details")
            if isinstance(details, dict):
                detail = _to_int(details.get("cached_tokens"))
            if detail <= 0:
                detail = _to_int(usage.get("cached_tokens"))
            cached += detail

            if not to_session:
                continue

            ts = _to_ms(record.get("timestamp"))
            if ts > 0:
                if min_ts == 0 or ts < min_ts:
                    min_ts = ts
                if ts >= max_ts:
                    max_ts = ts
                    last_prompt = prompt_tokens
            if isinstance(provider, dict):
                name = provider.get("model")
                if isinstance(name, str) and name:
                    model = name
            if completion > 0 and ts >= keep_after:
                samples.append((ts, completion))

        with self._state_lock:
            # 历史口径只由历史扫描写入，避免会话扫描重复累加
            if to_history:
                self._h_hit += hit
                self._h_miss += miss
                self._h_prompt += prompt
                self._h_cached += cached
                self._h_out += out
                self._h_records += records

            if to_session:
                self._s_hit += hit
                self._s_miss += miss
                self._s_prompt += prompt
                self._s_cached += cached
                self._s_out += out
                self._s_records += records
                if min_ts and (self._s_min_ts == 0 or min_ts < self._s_min_ts):
                    self._s_min_ts = min_ts
                if max_ts > self._s_max_ts:
                    self._s_max_ts = max_ts
                    self._s_last_prompt = last_prompt
                if model:
                    self._s_model = model
                if samples:
                    self._samples.extend(samples)

    @staticmethod
    def _collect(root):
        """递归收集 *.jsonl 的 (mtime, path, size)。"""
        found = []
        stack = [root]
        while stack:
            current = stack.pop()
            try:
                with os.scandir(current) as iterator:
                    for entry in iterator:
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                stack.append(entry.path)
                            elif entry.name.endswith(".jsonl"):
                                info = entry.stat()
                                found.append(
                                    (info.st_mtime, entry.path, info.st_size)
                                )
                        except OSError:
                            continue
            except OSError:
                continue
        return found

    # -- 汇总 ---------------------------------------------------------------

    def _build(self, now):
        """在 _state_lock 保护下汇总，纯内存操作。"""
        # 速率：最近 60 秒窗口内 completion_tokens 之和 ÷ 窗口首尾跨度
        now_ms = now * 1000.0
        cutoff = now_ms - SPEED_WINDOW_MS
        keep = now_ms - SPEED_KEEP_MS
        while self._samples and self._samples[0][0] < keep:
            self._samples.popleft()

        window = [s for s in self._samples if s[0] >= cutoff]
        speed = None
        if len(window) >= 2:
            stamps = [s[0] for s in window]
            span = (max(stamps) - min(stamps)) / 1000.0
            if span >= 1.0:
                speed = sum(s[1] for s in window) / span
                if speed < 0.0:
                    speed = None

        duration = 0.0
        if self._s_min_ts and self._s_max_ts > self._s_min_ts:
            duration = (self._s_max_ts - self._s_min_ts) / 1000.0

        return {
            # 会话口径
            "hit_rate": _ratio(
                self._s_hit, self._s_miss, self._s_prompt, self._s_cached
            ),
            "total_out": self._s_out,
            "records": self._s_records,
            "hit_tokens": self._s_hit,
            "miss_tokens": self._s_miss,
            "context_tokens": self._s_last_prompt,
            "session_duration": duration,
            "model": self._s_model,
            # 历史口径
            "history_hit_rate": _ratio(
                self._h_hit, self._h_miss, self._h_prompt, self._h_cached
            ),
            "history_out": self._h_out,
            "history_records": self._h_records,
            "history_hit_tokens": self._h_hit,
            "history_miss_tokens": self._h_miss,
            "history_ready": self._h_ready,
            # 通用
            "speed": speed,
            "files": self._file_count,
            "active_file": self._active_file,
            "updated_at": now,
            "error": None,
        }

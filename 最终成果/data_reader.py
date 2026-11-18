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

#: 会话口径增量阶段新出现的文件，最多回溯读取末尾这么多字节
#: （历史口径的新文件不受此限制，一律从 0 读，见 _consume 的注释）
NEW_FILE_TAIL_BYTES = 4 * 1024 * 1024

#: 速率的滑动窗口（毫秒）
SPEED_WINDOW_MS = 60 * 1000

#: 样本保留时长（毫秒），比窗口更长，便于平滑过期判定
SPEED_KEEP_MS = 180 * 1000

#: 空闲判定阈值（毫秒）：窗口内最新样本比「现在」早这么多，视为已停止生成，
#: 速率置空并由界面显示占位，避免停在旧数值上误导
SPEED_IDLE_MS = 15000

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


#: state 里按文件累计的明细字段：截断回滚与增量应用都以它为准
_DETAIL_FIELDS = ("hit", "miss", "prompt", "cached", "out", "records")


def _empty_detail():
    """一份全 0 的文件明细。"""
    return {field: 0 for field in _DETAIL_FIELDS}


def _detail_of(state):
    """取出 state 里已累计的明细；旧结构缺少字段时按 0 处理。"""
    return {field: _to_int(state.get(field)) for field in _DETAIL_FIELDS}


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

        files_map 决定 offset 与明细的归属（会话表或历史表），因此同一文件
        可以被两套统计各自读取一次，互不影响计数。to_history 为 False 时本批
        数据不计入历史口径（会话扫描只喂会话计数，历史计数只由历史扫描写入）。

        state 里除 offset/size/boundary 外，还按文件累计该文件自被统计以来的
        明细（hit/miss/prompt/cached/out/records）。文件被截断或重写时，先把
        这份明细从全局计数器里扣回去再从头读，重读才不会把旧数据算两遍。

        boundary 记录 offset 是否正好落在行首：True 表示下一批可以直接解析，
        False 表示 offset 落在行中（只出现在会话口径的新文件尾部回溯）。
        """
        state = files_map.get(path)
        first = state is None

        if first:
            detail = _empty_detail()
            # 历史口径的新文件一律从头读：靠既有 budget/deadline 分几轮补完，
            # 保证头部数据最终全部入账；只有会话口径允许尾部回溯——会话只关心
            # 当前对话的近期缓存效率，4 MB 足够覆盖，头部永久不入账是可接受
            # 的取舍（换取首次增量不被大文件拖慢）。
            if to_history:
                start = 0
            else:
                start = 0 if full else max(0, size - NEW_FILE_TAIL_BYTES)
            boundary = start == 0      # 从 0 开始必为行首
        else:
            detail = _detail_of(state)
            start = state["offset"]
            boundary = state.get("boundary", True)   # 旧结构按行首处理
            if size < start:          # 文件被截断/重写
                # 先把本文件此前计入的明细扣回去，再从头重读。会话口径的
                # min_ts/max_ts/last_prompt/model 与采样队列无法按文件回滚，
                # 只有计数类指标回滚，速率曲线可能短暂失真（可接受）。
                self._rollback(detail, to_session, to_history)
                detail = _empty_detail()
                start = 0
                boundary = True

        if size <= start:
            self._store(files_map, path, start, size, boundary, detail)
            return 0

        want = size - start
        if budget is not None and want > budget:
            want = budget

        with open(path, "rb") as handle:
            handle.seek(start)
            data = handle.read(want)
        if not data:
            self._store(files_map, path, start, size, boundary, detail)
            return 0

        base = start
        payload = data

        # 只有在 offset 不在行首时，首行才是半行，丢弃到第一个换行为止
        if not boundary and start > 0:
            newline = payload.find(b"\n")
            if newline < 0:
                self._store(files_map, path, start, size, boundary, detail)
                return len(data)
            base = start + newline + 1
            payload = payload[newline + 1:]

        # 只处理完整行，尾部残行留到下次，避免半截 JSON 与重复计数
        last_newline = payload.rfind(b"\n")
        if last_newline < 0:
            # 一条完整行都没有：offset 原地不动，等这条行写完整再解析
            self._store(files_map, path, start, size, boundary, detail)
            return len(data)

        batch = self._parse(payload[:last_newline + 1], to_session, to_history)
        new_detail = {
            field: detail[field] + batch[field] for field in _DETAIL_FIELDS
        }
        self._apply(batch, to_session, to_history)
        self._store(
            files_map, path, base + last_newline + 1, size, True, new_detail
        )
        return want

    @staticmethod
    def _store(files_map, path, offset, size, boundary, detail):
        """写入单个文件的读取状态，明细随状态一起保存。"""
        state = {"offset": offset, "size": size, "boundary": boundary}
        state.update(detail)
        files_map[path] = state

    def _apply(self, batch, to_session, to_history):
        """把一批解析结果加到全局计数器（锁内执行，不碰磁盘）。"""
        with self._state_lock:
            # 历史口径只由历史扫描写入，避免会话扫描重复累加
            if to_history:
                self._h_hit += batch["hit"]
                self._h_miss += batch["miss"]
                self._h_prompt += batch["prompt"]
                self._h_cached += batch["cached"]
                self._h_out += batch["out"]
                self._h_records += batch["records"]

            if to_session:
                self._s_hit += batch["hit"]
                self._s_miss += batch["miss"]
                self._s_prompt += batch["prompt"]
                self._s_cached += batch["cached"]
                self._s_out += batch["out"]
                self._s_records += batch["records"]
                self._apply_session_extras(batch)

    def _apply_session_extras(self, batch):
        """更新会话口径的时间范围/上下文/模型/速率样本（调用方已持锁）。"""
        min_ts = batch["min_ts"]
        max_ts = batch["max_ts"]
        if min_ts and (self._s_min_ts == 0 or min_ts < self._s_min_ts):
            self._s_min_ts = min_ts
        if max_ts > self._s_max_ts:
            self._s_max_ts = max_ts
            self._s_last_prompt = batch["last_prompt"]
        if batch["model"]:
            self._s_model = batch["model"]
        if batch["samples"]:
            self._samples.extend(batch["samples"])

    def _rollback(self, detail, to_session, to_history):
        """把某文件此前计入的明细从全局计数器扣回，max(0, …) 兜底不减成负数。"""
        with self._state_lock:
            if to_history:
                self._h_hit = max(0, self._h_hit - detail["hit"])
                self._h_miss = max(0, self._h_miss - detail["miss"])
                self._h_prompt = max(0, self._h_prompt - detail["prompt"])
                self._h_cached = max(0, self._h_cached - detail["cached"])
                self._h_out = max(0, self._h_out - detail["out"])
                self._h_records = max(0, self._h_records - detail["records"])

            if to_session:
                self._s_hit = max(0, self._s_hit - detail["hit"])
                self._s_miss = max(0, self._s_miss - detail["miss"])
                self._s_prompt = max(0, self._s_prompt - detail["prompt"])
                self._s_cached = max(0, self._s_cached - detail["cached"])
                self._s_out = max(0, self._s_out - detail["out"])
                self._s_records = max(0, self._s_records - detail["records"])

    def _parse(self, blob, to_session, to_history=True):
        """解析一批完整行，返回本批汇总，不写任何全局状态（可在锁外调用）。

        计数类字段总是有值；min_ts/max_ts/last_prompt/model/samples 只在
        to_session 为 True 时有意义（会话口径专用）。to_history 参数保留以
        维持调用签名，计数归属改由 _consume 在加总时决定。
        """
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

        return {
            "hit": hit,
            "miss": miss,
            "prompt": prompt,
            "cached": cached,
            "out": out,
            "records": records,
            "min_ts": min_ts,
            "max_ts": max_ts,
            "last_prompt": last_prompt,
            "model": model,
            "samples": samples,
        }

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
        # 速率：最近 60 秒窗口内 completion_tokens 之和 ÷ (现在 - 窗口最早样本)
        # 窗口锚到「现在」而非首尾样本，单条样本也能给出数值；窗口内最新样本
        # 已超过 SPEED_IDLE_MS 没更新，说明生成已停，速率置空并标记空闲。
        now_ms = now * 1000.0
        cutoff = now_ms - SPEED_WINDOW_MS
        keep = now_ms - SPEED_KEEP_MS
        while self._samples and self._samples[0][0] < keep:
            self._samples.popleft()

        window = [s for s in self._samples if s[0] >= cutoff]
        speed = None
        speed_idle = False
        if window:
            newest = max(s[0] for s in window)
            if now_ms - newest > SPEED_IDLE_MS:
                speed = None
                speed_idle = True
            else:
                span = max(1.0, (now_ms - min(s[0] for s in window)) / 1000.0)
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
            "speed_idle": speed_idle,
            "files": self._file_count,
            "active_file": self._active_file,
            "updated_at": now,
            "error": None,
        }

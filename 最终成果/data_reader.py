# -*- coding: utf-8 -*-
"""WorkBuddy 悬浮窗 —— 数据层（双口径 + 多会话版）。

只读扫描 ``~/.workbuddy/projects`` 下的 ``*.jsonl`` 会话日志，同时维护两套
互相独立的统计：

  * **会话口径**：一个或多个当前活跃会话（主日志 + 其 subagents）
  * **历史口径**：全部日志的全历史累计

会话口径默认只统计 mtime 最新的一个会话（单会话模式）；开启多会话模式后，
统计最近 ``ACTIVE_WINDOW_SEC`` 秒内有过写入的全部会话，最多保留
``MAX_SESSIONS`` 个，按最后写入时间倒序。每个会话有独立的偏移表与计数器，
互不干扰。

理由：会话口径反映「这次对话的缓存效率」，数值会明显波动；历史口径反映
「长期平均值」，稳定在 96% 上下。两者各有用途，由界面按用户设置选用。

加载策略：
  * 会话口径首次全量（通常 <1 秒），之后按 (size, offset) 增量
  * 历史口径首次全量（324 文件 / 354 MB 约 2~3 秒），之后每 4 个周期增量一次
  * 各统计各有独立的 offset 表，互不干扰

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

#: 多会话模式下，日志在这么多秒内有写入才算「同时进行」
ACTIVE_WINDOW_SEC = 10 * 60

#: 多会话模式最多统计的会话数（界面收起时按此上限显示，每会话两行）
MAX_SESSIONS = 3

#: 数据层保留的活跃会话上限。UI 可展开查看全部，故数据层需持有比
#: 「收起显示数」更多的会话统计；设置 15 足够覆盖正常多开场景。
MAX_SESSIONS_EXPANDED = 15

#: 速率的滑动窗口（毫秒）。深度思考/工具执行阶段常连续数分钟不产出 token，
#: 60 秒窗口会让速率几乎一直显示 --。放宽到 5 分钟，取「近期平均速率」，
#: 与 ZCode 侧口径一致（findings.md P21）。
SPEED_WINDOW_MS = 5 * 60 * 1000

#: 样本保留时长（毫秒），比窗口更长，便于平滑过期判定
SPEED_KEEP_MS = 10 * 60 * 1000

#: 空闲判定阈值（毫秒）：必须略大于 SPEED_WINDOW_MS，保证窗口内还有样本时
#: 不会被误判为空闲；超过 6 分钟完全无输出才显示空闲
SPEED_IDLE_MS = 6 * 60 * 1000

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


class _SessionStats(object):
    """单个会话的独立统计与文件偏移表。

    所有方法都由 UsageReader 在持有 _state_lock 时调用，因此内部不再加锁。
    """

    def __init__(self, main_path):
        self.main_path = main_path
        self.session_id = os.path.splitext(os.path.basename(main_path))[0]
        self.project = os.path.basename(os.path.dirname(main_path))
        self.files = {}             # 本会话文件 -> {"offset", "size", ...}
        self.hit = 0
        self.miss = 0
        self.prompt = 0
        self.cached = 0
        self.out = 0
        self.records = 0
        self.min_ts = 0             # 会话内最早记录时间（毫秒）
        self.max_ts = 0             # 会话内最新记录时间（毫秒）
        self.last_prompt = 0        # 最新一条请求的 prompt_tokens
        self.model = ""             # 当前会话模型
        self.title = ""             # 日志里 ai-title 给出的会话标题
        self.title_ts = 0
        self.last_mtime = 0.0       # 最近一次扫描到的文件写入时间（秒）
        self.samples = deque()      # (timestamp_ms, completion_tokens)

    def apply(self, batch):
        """把一批解析结果累加到本会话。"""
        self.hit += batch["hit"]
        self.miss += batch["miss"]
        self.prompt += batch["prompt"]
        self.cached += batch["cached"]
        self.out += batch["out"]
        self.records += batch["records"]

        min_ts = batch["min_ts"]
        max_ts = batch["max_ts"]
        if min_ts and (self.min_ts == 0 or min_ts < self.min_ts):
            self.min_ts = min_ts
        if max_ts > self.max_ts:
            self.max_ts = max_ts
            self.last_prompt = batch["last_prompt"]
        if batch["model"]:
            self.model = batch["model"]
        if batch["title"] and batch["title_ts"] >= self.title_ts:
            self.title = batch["title"]
            self.title_ts = batch["title_ts"]
        if batch["samples"]:
            self.samples.extend(batch["samples"])

    def rollback(self, detail):
        """文件被截断重读前，把本文件此前的明细扣回。"""
        self.hit = max(0, self.hit - detail["hit"])
        self.miss = max(0, self.miss - detail["miss"])
        self.prompt = max(0, self.prompt - detail["prompt"])
        self.cached = max(0, self.cached - detail["cached"])
        self.out = max(0, self.out - detail["out"])
        self.records = max(0, self.records - detail["records"])

    def row(self, now, now_ms, cutoff, keep):
        """生成该会话的指标行（纯内存，调用方持锁）。"""
        while self.samples and self.samples[0][0] < keep:
            self.samples.popleft()

        window = [s for s in self.samples if s[0] >= cutoff]
        speed = None
        speed_idle = False
        if window:
            newest = max(s[0] for s in window)
            if now_ms - newest > SPEED_IDLE_MS:
                speed_idle = True
            else:
                span = max(1.0, (now_ms - min(s[0] for s in window)) / 1000.0)
                speed = sum(s[1] for s in window) / span
                if speed < 0.0:
                    speed = None

        duration = 0.0
        if self.min_ts and self.max_ts > self.min_ts:
            duration = (self.max_ts - self.min_ts) / 1000.0

        title = self.title or "%s · %s" % (
            self.project or "会话", self.session_id[:6]
        )
        return {
            "title": title,
            "project": self.project,
            "session_id": self.session_id,
            "hit_rate": _ratio(
                self.hit, self.miss, self.prompt, self.cached
            ),
            "speed": speed,
            "speed_idle": speed_idle,
            "total_out": self.out,
            "records": self.records,
            "hit_tokens": self.hit,
            "miss_tokens": self.miss,
            "context_tokens": self.last_prompt,
            "session_duration": duration,
            "model": self.model,
            "last_active": self.max_ts / 1000.0 if self.max_ts else 0.0,
            "updated_at": now,
        }


class UsageReader:
    """读取 WorkBuddy 会话日志，同时输出会话与历史两套指标。"""

    def __init__(self, projects_dir):
        self.projects_dir = projects_dir

        # 锁序固定为 _scan_lock -> _state_lock
        self._scan_lock = threading.Lock()
        self._state_lock = threading.Lock()

        # ---- 会话口径（按会话独立统计）----
        self._sessions = {}           # 主日志绝对路径 -> _SessionStats
        self._primary = None          # 最新活跃会话主日志
        self._session_total = 0       # 活跃会话总数（可能大于 MAX_SESSIONS）
        self._multi = False           # 多会话模式开关
        self._mode_dirty = False      # 模式刚切换，下一轮强制全量

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

    def set_multi(self, enabled):
        """切换单会话 / 多会话模式；下一轮扫描会自动做一次全量。

        只碰 _state_lock，不等待正在进行的磁盘扫描，避免设置保存时界面卡住。
        """
        enabled = bool(enabled)
        with self._state_lock:
            if enabled == self._multi:
                return
            self._multi = enabled
            self._mode_dirty = True

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
        # 1) 会话口径首次全量：只读活跃会话，通常 1 秒内完成
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

        with self._scan_lock:
            do_full = full
            with self._state_lock:
                multi = self._multi
                if self._mode_dirty:
                    do_full = True
                    self._mode_dirty = False

            groups = self._group_sessions(entries)
            if multi:
                now = time.time()
                active = [
                    g for g in groups
                    if 0 <= now - g["mtime"] <= ACTIVE_WINDOW_SEC
                ]
                total = len(active)
                chosen = active[:MAX_SESSIONS_EXPANDED]
            else:
                chosen = groups[:1]
                total = len(chosen)

            desired = dict((g["main"], g) for g in chosen)
            with self._state_lock:
                for key in [k for k in self._sessions if k not in desired]:
                    del self._sessions[key]
                for key, group in desired.items():
                    stats = self._sessions.get(key)
                    if stats is None:
                        # 新出现的会话必须从头读，保证累计口径完整
                        stats = _SessionStats(key)
                        self._sessions[key] = stats
                        do_full = True
                    stats.project = group["project"]
                    stats.last_mtime = group["mtime"]
                    group["stats"] = stats
                self._session_total = total
                self._file_count = sum(len(g["files"]) for g in chosen)
                self._primary = chosen[0]["main"] if chosen else None
                self._active_file = (
                    os.path.basename(self._primary) if self._primary else ""
                )

            # 文件消失时同步清理该会话的偏移表
            for group in chosen:
                keep = set(item[1] for item in group["files"])
                stats = group["stats"]
                for path in [p for p in stats.files if p not in keep]:
                    del stats.files[path]

            budget = None if do_full else SESSION_MAX_BYTES
            deadline = (
                None if do_full else time.perf_counter() + SESSION_TIME_BUDGET
            )
            # 跨会话按文件写入时间倒序消费，预算优先给最活跃的日志
            tasks = []
            for group in chosen:
                for item in group["files"]:
                    tasks.append((item[0], item[1], item[2], group["stats"]))
            tasks.sort(key=lambda task: task[0], reverse=True)

            for _mtime, path, size, stats in tasks:
                if deadline is not None and time.perf_counter() >= deadline:
                    break
                if budget is not None and budget <= 0:
                    break
                try:
                    used = self._consume(
                        path, size, budget, do_full, stats.files, stats, False
                    )
                except OSError:
                    continue
                if budget is not None:
                    budget -= used

        with self._state_lock:
            self._error = None

    @staticmethod
    def _group_sessions(entries):
        """把 (mtime, path, size) 列表按会话归组，并按写入时间倒序返回。"""
        marker = os.sep + "subagents" + os.sep
        groups = {}
        for mtime, path, size in entries:
            if marker in path:
                # 子代理日志归属到 <会话目录>.jsonl 这个主日志
                main = path.split(marker, 1)[0] + ".jsonl"
            else:
                main = path
            group = groups.get(main)
            if group is None:
                group = {
                    "main": main,
                    "mtime": mtime,
                    "project": os.path.basename(os.path.dirname(main)),
                    "files": [],
                }
                groups[main] = group
            group["files"].append((mtime, path, size))
            if mtime > group["mtime"]:
                group["mtime"] = mtime

        result = list(groups.values())
        result.sort(key=lambda item: item["mtime"], reverse=True)
        return result

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
                        path, size, budget, full, self._h_files, None, True
                    )
                except OSError:
                    continue
                if budget is not None:
                    budget -= used

    # -- 通用读取与解析 -----------------------------------------------------

    def _consume(self, path, size, budget, full, files_map, stats,
                 to_history):
        """读取单个文件的新增字节，返回本轮消耗的字节数。

        files_map 决定 offset 与明细的归属（某个会话或历史表），因此同一文件
        可以被多套统计各自读取一次，互不影响计数。stats 为 None 时本批数据
        只计入历史口径；to_history 为 False 时不计入历史口径（会话扫描只喂
        会话计数，历史计数只由历史扫描写入）。

        state 里除 offset/size/boundary 外，还按文件累计该文件自被统计以来的
        明细（hit/miss/prompt/cached/out/records）。文件被截断或重写时，先把
        这份明细从计数器里扣回去再从头读，重读才不会把旧数据算两遍。

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
                self._rollback(detail, stats, to_history)
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

        batch = self._parse(
            payload[:last_newline + 1], stats is not None
        )
        new_detail = {
            field: detail[field] + batch[field] for field in _DETAIL_FIELDS
        }
        self._apply(batch, stats, to_history)
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

    def _apply(self, batch, stats, to_history):
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

            if stats is not None:
                stats.apply(batch)

    def _rollback(self, detail, stats, to_history):
        """把某文件此前计入的明细从计数器扣回，max(0, …) 兜底不减成负数。"""
        with self._state_lock:
            if to_history:
                self._h_hit = max(0, self._h_hit - detail["hit"])
                self._h_miss = max(0, self._h_miss - detail["miss"])
                self._h_prompt = max(0, self._h_prompt - detail["prompt"])
                self._h_cached = max(0, self._h_cached - detail["cached"])
                self._h_out = max(0, self._h_out - detail["out"])
                self._h_records = max(0, self._h_records - detail["records"])

            if stats is not None:
                stats.rollback(detail)

    def _parse(self, blob, want_extras):
        """解析一批完整行，返回本批汇总，不写任何全局状态（可在锁外调用）。

        计数类字段总是有值；min_ts/max_ts/last_prompt/model/title/samples
        只在 want_extras 为 True 时有意义（会话口径专用）。to_history 归属
        改由 _consume 在加总时决定。
        """
        keep_after = time.time() * 1000.0 - SPEED_KEEP_MS
        hit = miss = prompt = cached = out = records = 0
        samples = []
        min_ts = 0
        max_ts = 0
        last_prompt = 0
        model = ""
        title = ""
        title_ts = 0

        for line in blob.split(b"\n"):
            if not line:
                continue
            if b'"rawUsage"' not in line and b'"ai-title"' not in line:
                continue
            try:
                record = json.loads(line.decode("utf-8", "replace"))
            except Exception:
                continue
            if not isinstance(record, dict):
                continue

            # 会话标题：日志里的 ai-title 记录，取时间最新的一条
            if record.get("type") == "ai-title":
                if want_extras:
                    text = record.get("aiTitle")
                    ts = _to_ms(record.get("timestamp"))
                    if isinstance(text, str) and text.strip():
                        if ts >= title_ts:
                            title = text.strip()
                            title_ts = ts
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

            if not want_extras:
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
            "title": title,
            "title_ts": title_ts,
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
        now_ms = now * 1000.0
        cutoff = now_ms - SPEED_WINDOW_MS
        keep = now_ms - SPEED_KEEP_MS

        ordered = sorted(
            self._sessions.values(),
            key=lambda stats: stats.last_mtime,
            reverse=True,
        )
        rows = [
            stats.row(now, now_ms, cutoff, keep) for stats in ordered
        ]
        primary = rows[0] if rows else {}

        result = {
            # 会话口径（最新会话，保持原有字段语义）
            "hit_rate": primary.get("hit_rate"),
            "total_out": primary.get("total_out", 0),
            "records": primary.get("records", 0),
            "hit_tokens": primary.get("hit_tokens", 0),
            "miss_tokens": primary.get("miss_tokens", 0),
            "context_tokens": primary.get("context_tokens", 0),
            "session_duration": primary.get("session_duration", 0.0),
            "model": primary.get("model", ""),
            "title": primary.get("title", ""),
            # 历史口径
            "history_hit_rate": _ratio(
                self._h_hit, self._h_miss, self._h_prompt, self._h_cached
            ),
            "history_out": self._h_out,
            "history_records": self._h_records,
            "history_hit_tokens": self._h_hit,
            "history_miss_tokens": self._h_miss,
            "history_ready": self._h_ready,
            # 多会话
            "sessions": rows,
            "session_count": self._session_total,
            "session_multi": self._multi,
            # 通用
            "speed": primary.get("speed"),
            "speed_idle": primary.get("speed_idle", False),
            "files": self._file_count,
            "active_file": self._active_file,
            "updated_at": now,
            "error": None,
        }
        return result

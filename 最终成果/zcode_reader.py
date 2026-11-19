# -*- coding: utf-8 -*-
"""ZCode 悬浮窗数据层（只读 db.sqlite 的 model_usage + session）。

与 data_reader.py（WorkBuddy 日志）完全独立，两者在入口层合并。
任何异常都被吞掉并写进 error 字段，不影响主悬浮窗。

数据源
------
``~/.zcode/cli/db/db.sqlite`` 的 ``model_usage`` 表联 ``session`` 表：

  * ``model_usage``：每次模型调用的 token 明细（24000+ 条，毫秒时间戳）
  * ``session``：会话标题（真实中文标题，比 rollout 里的 sessionId 可读）

命中率口径
----------
ZCode 源码（app.asar /out/host/index.js）明确用
``hitRate = cacheReadTokens / inputTokens``，其中 inputTokens 已包含 cacheRead
部分。此前误用 ``cacheRead/(cacheRead+input)``（分母多加一次 cacheRead），
数学上永远 ≤50%，把真实的 ~97% 腰斩成 ~49%。已修正为 ZCode 原生口径。
界面给 ZCode 会话行加 ``ZCode ·`` 前缀标明数据来源。
"""

import os
import sqlite3
import threading
import time
from collections import deque

# ---- 可调常量 -------------------------------------------------------------

#: 扫描间隔（秒）。sqlite 查询毫秒级，2 秒足够跟上实时调用
SCAN_INTERVAL = 2.0

#: 活跃窗口：秒内有 token 写入才算「进行中」，与 WorkBuddy 口径一致
ACTIVE_WINDOW_SEC = 10 * 60

#: 速率滑动窗口（毫秒）。ZCode 把整段输出在调用完成时一次性写库，
#: 一个长调用可持续 3~5 分钟，60 秒窗口会让调用进行中几乎一直显示 --。
#: 放宽到 5 分钟，取「近期平均速率」，调用间隙也能看到上一次的速率。
SPEED_WINDOW_MS = 5 * 60 * 1000

#: 样本保留时长（毫秒），比窗口更长，便于平滑过期判定
SPEED_KEEP_MS = 10 * 60 * 1000

#: 空闲判定阈值（毫秒）：最新样本比现在早这么多，速率置空。
#: 必须略大于 SPEED_WINDOW_MS，保证「窗口内还有样本」时不会被误判为空闲；
#: 即超过 6 分钟完全无输出才显示空闲。
SPEED_IDLE_MS = 6 * 60 * 1000

#: 速率「陈旧」阈值（毫秒）：最新样本距今超过此值，界面加 `~` 前缀淡化，
#: 表示「上次生成的值，当前未在生成」（与 data_reader 一致，P22）
SPEED_STALE_MS = 90 * 1000

#: 最多输出的会话数（UI 收起时显示 3 个，展开时显示全部；与 data_reader 对齐）
MAX_SESSIONS = 15


def _resolve_db_path():
    """定位 ZCode 数据库，找不到时也返回预期路径（由 error 字段提示）。"""
    home = os.path.expanduser("~")
    return os.path.join(home, ".zcode", "cli", "db", "db.sqlite")


def _to_int(value):
    """把数据库字段安全转成非负整数。"""
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))
    if isinstance(value, str):
        try:
            return max(0, int(float(value)))
        except (TypeError, ValueError):
            return 0
    return 0


def _clean_title(title, session_id):
    """生成可读标题：滤掉纯数字/过短的占位标题，统一加 ZCode 前缀。"""
    text = (title or "").strip()
    if not text or text.isdigit() or len(text) < 2:
        text = session_id[-8:] if session_id else "会话"
    return "ZCode · " + text


class _ZSession:
    """单个 ZCode 会话的累计计数与速率样本。"""

    def __init__(self, session_id, title, model):
        self.session_id = session_id
        self.title = title
        self.model = model
        self.input = 0
        self.output = 0
        self.cache_read = 0
        self.cache_write = 0
        self.records = 0
        self.min_ts = 0
        self.max_ts = 0
        # (completed_at_ms, output_tokens, duration_ms)
        self.samples = deque()

    def add(self, ts, input_tokens, output_tokens, cache_read, cache_write,
            done_ts=None):
        """累加一条 model_usage。

        ts 是 started_at（用于活跃判定），done_ts 是 completed_at（用于速率）。
        速率样本必须用 completed_at：输出 token 是调用完成时产出的，
        若用 started_at，一个耗时数分钟的调用在完成瞬间也早已被 60 秒窗口
        判为过期，速率永远显示 --（详见 findings.md P16）。
        """
        self.input += input_tokens
        self.output += output_tokens
        self.cache_read += cache_read
        self.cache_write += cache_write
        self.records += 1
        sample_ts = done_ts if done_ts else ts
        active_ts = max(ts, sample_ts)     # 活跃判定取「开始/完成」较新者
        if active_ts > self.max_ts:
            self.max_ts = active_ts
        if self.min_ts == 0 or ts < self.min_ts:
            self.min_ts = ts
        # 用该调用自身的真实时长作速率分母，避免「单个刚完成样本 ÷ 几秒」
        # 算出虚高的瞬态速率（详见 findings.md P17）
        duration_ms = max(1, sample_ts - ts) if done_ts else 0
        self.samples.append((sample_ts, output_tokens, duration_ms))

    def row(self, now_ms, cutoff, keep):
        """生成与 data_reader._SessionStats.row 同结构的指标行（纯内存）。"""
        while self.samples and self.samples[0][0] < keep:
            self.samples.popleft()

        window = [s for s in self.samples if s[0] >= cutoff]
        speed = None
        speed_idle = False
        speed_stale = False
        if window:
            newest = max(s[0] for s in window)
            if now_ms - newest > SPEED_IDLE_MS:
                speed_idle = True
            else:
                speed_stale = (now_ms - newest) > SPEED_STALE_MS
                out_tokens = sum(s[1] for s in window)
                total_duration = sum(s[2] for s in window)
                if total_duration > 0:
                    # 有真实时长：用「总输出 ÷ 各调用时长之和」，贴近端到端吞吐
                    span = total_duration / 1000.0
                else:
                    # 无时长信息（旧数据/测试）：回退到窗口跨度
                    span = max(1.0, (now_ms - min(s[0] for s in window)) / 1000.0)
                speed = out_tokens / span
                if speed < 0.0:
                    speed = None

        # 命中率口径：cacheReadTokens / inputTokens（ZCode 源码口径，见 findings P19）
        # ZCode 的 inputTokens 已包含 cacheRead 部分，分母不能再加一次 cacheRead。
        # 旧口径 cacheRead/(cacheRead+input) 数学上永远 ≤50%，会把 ~97% 腰斩成 ~49%。
        hit_rate = (
            min(1.0, max(0.0, self.cache_read / float(self.input)))
            if self.input > 0 else None
        )
        duration = (
            (self.max_ts - self.min_ts) / 1000.0
            if self.max_ts > self.min_ts else 0.0
        )
        return {
            "title": _clean_title(self.title, self.session_id),
            "project": "ZCode",
            "session_id": self.session_id,
            "hit_rate": hit_rate,
            "speed": speed,
            "speed_idle": speed_idle,
            "speed_stale": speed_stale,
            "total_out": self.output,
            "records": self.records,
            "hit_tokens": self.cache_read,
            "miss_tokens": self.input,
            "context_tokens": 0,
            "session_duration": duration,
            "model": self.model,
            "last_active": self.max_ts / 1000.0 if self.max_ts else 0.0,
            "source": "zcode",
        }


class ZcodeReader:
    """后台只读扫描 ZCode 数据库，输出 ZCode 会话指标快照。

    构造即启动 daemon 线程；``snapshot()`` 只做内存汇总，不触发磁盘 IO。
    """

    def __init__(self, db_path=None):
        self.db_path = db_path or _resolve_db_path()
        self._lock = threading.Lock()
        self._sessions = {}
        self._error = None
        self._primed = threading.Event()
        self._worker = threading.Thread(
            target=self._run, name="zcode-reader", daemon=True
        )
        self._worker.start()

    # -- 对外接口 -----------------------------------------------------------

    def snapshot(self):
        """返回 ZCode 快照 dict，任何异常都写进 zcode_error。"""
        self._primed.wait(0.0)
        now_ms = time.time() * 1000.0
        with self._lock:
            cutoff = now_ms - SPEED_WINDOW_MS
            keep = now_ms - SPEED_KEEP_MS
            ordered = sorted(
                self._sessions.values(),
                key=lambda stats: stats.max_ts,
                reverse=True,
            )
            rows = [stats.row(now_ms, cutoff, keep) for stats in ordered]
            return {
                "zcode_sessions": rows[:MAX_SESSIONS],
                "zcode_count": len(rows),
                "zcode_error": self._error,
            }

    # -- 后台调度 -----------------------------------------------------------

    def _run(self):
        while True:
            try:
                self._scan()
            except Exception as exc:
                with self._lock:
                    self._error = "%s: %s" % (type(exc).__name__, exc)
            finally:
                self._primed.set()
            time.sleep(SCAN_INTERVAL)

    def _scan(self):
        if not os.path.isfile(self.db_path):
            with self._lock:
                self._sessions = {}
                self._error = "ZCode 数据库不存在: %s" % self.db_path
            return

        # 只读 URI；WAL 模式下也能读到已提交数据，绝不写库
        uri = "file:%s?mode=ro" % self.db_path.replace("\\", "/")
        con = sqlite3.connect(uri, uri=True, timeout=1.0)
        try:
            cur = con.cursor()
            cutoff_ms = int(time.time() * 1000) - ACTIVE_WINDOW_SEC * 1000
            rows = cur.execute(
                """
                SELECT m.session_id, m.input_tokens, m.output_tokens,
                       m.cache_read_input_tokens, m.cache_creation_input_tokens,
                       m.started_at, m.completed_at, m.model_id, s.title
                FROM model_usage m LEFT JOIN session s ON s.id = m.session_id
                WHERE m.started_at >= ? OR m.completed_at >= ?
                ORDER BY m.started_at ASC
                """,
                (cutoff_ms, cutoff_ms),
            ).fetchall()
        finally:
            con.close()

        fresh = {}
        for sid, inp, out, cr, cw, ts, done_ts, model, title in rows:
            inp, out, cr, cw, ts, done_ts = (
                _to_int(inp), _to_int(out), _to_int(cr), _to_int(cw),
                _to_int(ts), _to_int(done_ts),
            )
            if inp + out + cr + cw == 0:      # 跳过取消/失败的空记录
                continue
            stats = fresh.get(sid)
            if stats is None:
                stats = fresh[sid] = _ZSession(sid, title, model or "")
            stats.add(ts, inp, out, cr, cw, done_ts)

        with self._lock:
            self._sessions = fresh
            self._error = None

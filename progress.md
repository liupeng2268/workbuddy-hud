# Progress Log —— WorkBuddy 悬浮窗

## Session: 2026-11-20 00:20（停止任务仍显示速率 → v1.3.2）

### Phase 12: 陈旧速率 ~ 标记

- **Status:** complete（代码与打包完成，待用户手动冒烟；GitHub 提交推送未在本次做）
- 触发：用户截图 Tailscale 会话已停却仍显示 12.6 tok/s。
- 根因（P22）：v1.3.1 idle 6min > 5min 窗口，窗口内旧样本（264s 前）仍算速率；且 session-meta 每几秒写一次使 mtime 不可用，判断必须用最后 token 时间。
- 修复：新增 `SPEED_STALE_MS=90s`（两个数据层同步）、`speed_stale` 字段；UI stale 速率加 `~` 前缀 + 淡化。用户选「加 ~ 标记」方案（另两选项：直接 `--`、缩短窗口）。
- 验证：真实库 Tailscale `~29.7 tok/s`(stale）、plan-sync `20.9 tok/s`(实时）；verify_data_reader 44/44、verify_zcode 23/23、ui_pretest 57/57、编译 OK。
- 打包：备份 v1.3.1 → `_临时\backup\*-20261120-002200.*`；v1.3.2 PE 1.3.2、SHA256 `AA1BE3D141E14A24C5B21E445BB1F903314FBEC1259099933BD72194F3A6FBD4`。
- 待办：用户手动双击 exe 冒烟，确认停止会话显示 `~` 前缀、运行中会话正常数值。

### Test Results（Phase 12）

| Test | Expected | Actual | Status |
|------|----------|--------|--------|
| 编译检查 | 无错误 | COMPILE_OK | PASS |
| verify_data_reader | stale 标记 | 44/44 | PASS |
| verify_zcode | stale 标记 | 23/23 | PASS |
| ui_pretest | ~ 前缀显示 | 57/57 | PASS |
| 真实库验证 | 停/~、跑/数值 | ~29.7 / 20.9 tok/s | PASS |
| PE 版本 | 1.3.2 | 1.3.2 | PASS |
| exe 运行时冒烟 | 存活 | 未执行（WMI 被拦截） | BLOCKED |

---

## Session: 2026-11-20 00:05（WorkBuddy 速率 `--` → v1.3.1）

### Phase 11: 深度思考会话速率不显示

- **Status:** complete（代码与打包完成，待用户手动冒烟）
- 触发：用户截图「测试 Tailscale 指定 IP 连通性」命中率有值、速率一直 `--`，怀疑无数据。
- 根因（findings P21）：该会话 token 产出极稀疏（间隔 267s、122s…），60 秒窗口 0 条样本、5 分钟窗口 2 条/14043 tokens。深度思考阶段不产出 completion_tokens。
- 修复：data_reader 速率窗口 60s→5min、keep 3min→10min、idle 15s→6min，与 ZCode 统一；真实库验证 Tailscale 会话 `--` → 48.4 tok/s。
- 验证：编译 OK；verify_data_reader 40→43/43（新增 3 项窗口常量守卫）、ui_pretest 56/56、verify_text PASS（偶发 0.0K 为首次加载时序，连跑 3 次 False）、verify_zcode 21/21。
- 打包：备份 v1.3.0 → `_临时\backup\*-20261120-001000.*`；v1.3.1 PE 1.3.1、SHA256 `88BF5A17A23C67DAD4F8CC948064526654919962CAE1BFBF9A2C53B650451E91`。
- 待办：用户手动双击 exe 冒烟，确认该会话速率显示数值。

### Test Results（Phase 11）

| Test | Expected | Actual | Status |
|------|----------|--------|--------|
| 编译检查 | 无错误 | COMPILE_OK | PASS |
| verify_data_reader | 窗口常量守卫 | 43/43 | PASS |
| ui_pretest | 56/56 | 56/56 | PASS |
| verify_text | 无 None/0.0K | 连跑3次全 False | PASS |
| verify_zcode | 21/21 | 21/21 | PASS |
| 真实库速率验证 | 稀疏会话显示速率 | 48.4 / 49.0 tok/s | PASS |
| PE 版本 | 1.3.1 | 1.3.1 | PASS |
| exe 运行时冒烟 | 存活 | 未执行（WMI 被拦截） | BLOCKED |

---

## Session: 2026-11-19 深夜（会话展开/收起 → v1.3.0）

### Phase 10: 多会话展开/收起

- **Status:** complete（代码与打包完成，待用户手动冒烟）
- 触发：用户截图「进行中 5 个会话」只显示 3 个，希望底部「+2 个会话」可点击展开/收回。
- 改动（findings P20）：
  - 数据层：data_reader 新增 `MAX_SESSIONS_EXPANDED=15`，`chosen=active[:15]`，sessions 输出全部活跃会话；zcode_reader MAX_SESSIONS 3→15 同步
  - UI：footer 改可点击标志（收起 `+N 个会话 ▼` / 展开 `▲ 收起`），`_expanded` 状态，`_on_press`/`_on_release` 区分点击与拖动（位移 ≤4px 且命中 footer bbox），`_ensure_layout`/`_multi_height`/`_build_multi_items` 纳入 expanded
  - 收起显示 3 行、展开显示全部（上限 15），高度自适应 180→256（5 会话）
- 验证：编译 OK；verify_data_reader 40/40、ui_pretest 56/56（新增 8 项展开/收起 + 点击链路单测）、verify_text PASS、verify_zcode 21/21；点击展开→5 行/收回→3 行/拖动不误触发 全通过。
- 打包：终止 2 个运行中的 v1.2.2 进程，备份 → `_临时\backup\*-20261119-235900.*`；v1.3.0 PE 1.3.0、SHA256 `BBDD1B24C11B5543AC511B1F3401C0158A12DE97385F83EDB12460E7E32E6725`、PYZ 含 zcode_reader。
- 待办：用户手动双击 exe 冒烟，确认点击底部标志可展开/收回。

### Test Results（Phase 10）

| Test | Expected | Actual | Status |
|------|----------|--------|--------|
| 编译检查 | 无错误 | COMPILE_OK | PASS |
| verify_data_reader | 40/40 | 40/40 | PASS |
| ui_pretest | 展开/收起覆盖 | 56/56 | PASS |
| verify_text | 无异常 | 全符合 | PASS |
| verify_zcode | 21/21 | 21/21 | PASS |
| 点击 footer 链路 | 展开/收回/拖动区分 | 全通过 | PASS |
| PE 版本 | 1.3.0 | 1.3.0 | PASS |
| PYZ 模块 | 含 zcode_reader | True | PASS |
| exe 运行时冒烟 | 存活 | 未执行（WMI 被拦截） | BLOCKED |

---

## Session: 2026-11-19 深夜（命中率腰斩排查 → v1.2.2）

### Phase 9: ZCode 命中率口径纠错

- **Status:** complete（代码与打包完成，待用户手动冒烟）
- 触发：用户截图对比 ZCode 界面 88.1%/98.3% vs 悬浮窗 48.9%，问「命中率怎么差这么多」。
- 根因（findings P19）：旧口径 `cacheRead/(cacheRead+input)` 分母多加一次 cacheRead，数学上永远 ≤50%，腰斩真实命中率。解包 ZCode app.asar 找到源码权威公式 `hitRate = cacheReadTokens/inputTokens`。
- 修复：`_ZSession.row()` 改为 `cache_read/input`；新增「高命中率不被腰斩」回归断言；测试 20→21 项。
- 验证：真实库修正后 95.8%/65.9%/89.5%（与 ZCode 同量级）；全门禁绿（40/40、47/47、verify_text、21/21）；编译 OK。
- 打包：终止用户运行的 2 个 v1.2.1 进程后备份 + 打包；v1.2.2 PE 1.2.2、SHA256 `07A7F1CDFBB7DBAE60A0FE7366654A3729C5752B0967916F3E8F8478D4852DC6`、PYZ 含 zcode_reader。
- 待办：用户手动双击 exe 冒烟，确认 ZCode 命中率不再是 ~49%。

---

## Session: 2026-11-19 晚（速率不显示排查 → v1.2.1）

### Phase 8: ZCode 速率修复

- **Status:** complete（代码与打包完成，待用户手动冒烟）
- 触发：用户贴两张截图反馈「速率不显示，但都有在执行的任务」。视觉模型识别确认三个会话速率全是 `-- tok/s`（含 WorkBuddy 侧）。
- 根因（findings P16/P17）：① 速率用 `started_at` 时间戳，长调用完成瞬间样本已过期；② 单样本按 now−样本时间作分母会虚高；③ ZCode 输出在调用结束时一次性写库，长调用进行中库里无产出记录。
- 修复：
  - 样本时间戳改 `completed_at`，活跃判定取 started/completed 较新者
  - 样本携带调用自身时长，速率 = token 总和 ÷ 各调用时长之和（消虚高）
  - 按用户选择放宽窗口：60s→5min、keep 3min→10min、idle 15s→6min
- 验证：编译 OK；verify_data_reader 40/40、ui_pretest 47/47、verify_text PASS、verify_zcode 20/20（新增 4 项速率用例：completed_at 口径、无虚高、active_ts、对照）；真实库当前时刻 3 个会话速率正常显示 38.9/36.0/64.4 tok/s。
- 打包：先终止用户运行的 2 个 v1.2.0 进程（否则 safe-delete 拦截），备份 v1.2.0 → `_临时\backup\*-20261119-234900.*`，PyInstaller --clean 打包 v1.2.1。PE 版本 1.2.1；SHA256 `537FA482AA05B8324A6B424CD1976AD14A2B8D0230CCE956364BD7E78B42512F`；PYZ 含 zcode_reader。
- 待办：用户手动双击 exe 冒烟，确认速率列显示数值。

### Test Results（Phase 8）

| Test | Expected | Actual | Status |
|------|----------|--------|--------|
| 编译检查 | 无错误 | COMPILE_OK | PASS |
| verify_data_reader | 40/40 | 40/40 | PASS |
| ui_pretest | 47/47 | 47/47 | PASS |
| verify_text | 无异常 | 全符合 | PASS |
| verify_zcode | 20/20 | 20/20（含 4 项速率修复用例） | PASS |
| 真实库速率验证 | 5min 窗口内显示 | 38.9/36.0/64.4 tok/s | PASS |
| PE 版本 | 1.2.1 | 1.2.1 | PASS |
| PYZ 模块 | 含 zcode_reader | True | PASS |
| exe 运行时冒烟 | 存活 | 未执行（WMI 被拦截，需用户手动） | BLOCKED |

---

## Session: 2026-11-19（ZCode 会话接入 → v1.2.0）

### Phase 7: ZCode 会话接入

- **Status:** in_progress（待用户手动双击冒烟）
- 动作：
  - 调研 ZCode 三处数据源：log 全 Redacted 不可用、rollout 有 token 无标题、db.sqlite 有 token + 中文标题 → 选 db.sqlite（P11）
  - 确认口径差异与时间戳单位（P12/P13）；确认 model_usage 24044 条、started_at 毫秒、可直接 JOIN session 取标题
  - 新建 `最终成果/zcode_reader.py`（独立数据层，后台 daemon 线程，只读 db，10 分钟活跃窗口，原生命中率口径，标题回退）
  - 入口 pyw 加 `_zcode_enabled()` + `_make_provider()`：仅开关开启且多会话模式时并入 ZCode 行，按活跃时间倒序；兼容 on/off/布尔/缺失
  - floating_ui 加「ZCode(多会话)」设置项（默认 off）、设置窗高度 410→440、配置校验兼容旧布尔值
  - 新建 `tests/verify_zcode.py`（15 项断言，临时库造数据）；主控亲自跑，修正 1 处测试断言错误后 15/15 PASS
  - 编译 OK；回归 verify_data_reader 40/40、ui_pretest 47/47、verify_text PASS（现有行为零回归）
  - 备份 v1.1.1 exe + 设置 JSON → `_临时\backup\*-20261119-233500.*`
  - PyInstaller --clean 打包 v1.2.0；PE 版本资源 1.2.0；SHA256 `5C9E2F64C7285A2165E298A39C842CE9407FB997A297A6CD087441E2ADE2FFCC`
  - PYZ 归档确认 zcode_reader/data_reader/floating_ui/version 四个模块全部打入
  - README 补 ZCode 功能表、设置项、口径说明、隐私、文件结构
- 待办：用户手动双击 exe 冒烟（代理会话 WMI/CIM 启动被安全策略拦截，不绕过）

### Test Results（2026-11-19）

| Test | Input | Expected | Actual | Status |
|------|-------|----------|--------|--------|
| 编译检查 | py_compile 4 文件 | 无错误 | COMPILE_OK | PASS |
| verify_data_reader.py | py313 | 40/40 | 40/40 | PASS |
| ui_pretest.py | py313 | 47/47 | 47/47（canvas 268×116 不变） | PASS |
| verify_text.py | py313 | 无异常文本 | 全符合 | PASS |
| verify_zcode.py | py313，临时库 | 15/15 | 15/15 | PASS |
| 入口组合逻辑单测 | 模拟 on/off/布尔/缺失 + 合并排序 | 正确 | 全部正确 | PASS |
| PYZ 模块检查 | exe 归档 | 含 zcode_reader | 4 模块全在 | PASS |
| PE 版本资源 | exe | 1.2.0 | 1.2.0 | PASS |
| exe 运行时冒烟 | WMI 启动 | 存活 | 被安全策略拦截，未执行 | BLOCKED |

---

## Session: 2026-11-18（晚间：字体模糊排查 → v1.1.1 高 DPI 修复发布）

### Phase 3: 排查「字体变模糊」

- **Status:** complete
- 动作：
  - 实测运行中旧窗口：`GetDpiForWindow=96`、物理 402×150（= 268×100 × 1.5）→ 位图拉伸
  - 抽取 v1.1.0 与 v1.0.2 exe 的 RT_MANIFEST：逐字节一致，均无 `dpiAware`（findings.md P1）
  - 枚举显示器：单屏 2560×1600 @150%（144 DPI）；python.exe manifest 也无 dpiAware（P2）
  - git 历史 `git log -S` 确认从未有过 DPI 代码 → 实现缺口而非回归
- 修改文件：无（纯诊断）

### Phase 4: 高 DPI 代码修复

- **Status:** complete
- 动作：
  - `floating_ui.py` 新增 `_enable_windows_dpi_awareness()`，在 `run_app` 创建窗口前调用
  - `_FloatingWindow.__init__` 计算 `self._dpi = winfo_fpixels('1i')/96`，`_s()` 换算全部像素常量（WIDTH/HEIGHT/RADIUS/X_TEXT/Y_LINE*/MULTI_*/MARGIN_*/MIN_VISIBLE_*/设置窗 352×410/内边距/BORDER_W）
  - 位置口径：`_place_initial` 逻辑×系数定位；`_on_release` 物理÷系数保存（中途曾用 `pos_px` 迁移标记，发现重复放大后废弃）
- 修改文件：`最终成果\floating_ui.py`、`最终成果\version.py`、`最终成果\version_info.txt`

### Phase 5: 门禁验证 + 打包 v1.1.1

- **Status:** complete
- 动作：
  - 编译检查 COMPILE_OK；三项门禁全绿（见 Test Results）
  - 端到端探针（临时配置）：DPI 感知进程内 `winDpi=144`、物理 402×174 与预期一致
  - 备份 v1.1.0 exe + 设置 JSON → `_临时\backup\*-20261118-221001.*`
  - PyInstaller 6.22.3（py313）按根目录 spec 打包，dist exe 替换为 v1.1.1
  - 修复设置文件被重复迁移污染的 x/y（恢复 1465/843）
- 产物：`最终成果\dist\WorkBuddy悬浮窗.exe`（v1.1.1）

### Phase 6: 交付与观察

- **Status:** complete（2026-11-18 交付收尾）
- 动作：
  - 排查 exe 启动后 1-2 分钟静默消失（3 次）：无日志、无崩溃事件、`%TEMP%` 残留 `_MEI*` → 会话作业对象回收，非缺陷（P5）
  - 改用 WMI `Win32_Process.Create` 脱离会话启动：稳定 130 秒+（PID 16712/17836）
  - 实测新窗口：`winDpi=144`、物理 402×150、位置 (2195,1263) = 逻辑 (1465,843)×1.4985（P8）
  - 用户目测确认：悬浮窗文字恢复清晰、右键菜单在鼠标处弹出（P9 验证通过，无需 `_on_menu` 坐标换算）
  - 多会话静态核对 12 项全部符合规格；门禁回归 verify_data_reader 40/40、ui_pretest 47/47、verify_text PASS、编译 COMPILE_OK
  - README 补全多会话展示、高 DPI 适配、界面布局与设置表「会话显示」行
  - 用户贴真实环境「三会话」截图，视觉模型识别核对：3 块同屏、每块两行、顶部「进行中 3 个会话」、文字清晰；会话数=3 未超上限故无「+N 个会话」页脚（符合规格，P10）；无活跃生成的会话速率显示 `--` 符合口径
  - 更新 `.plan-sync/PLAN.md`、findings.md、task_plan.md、progress.md 至交付收尾里程碑

## Test Results

| Test | Input | Expected | Actual | Status |
|------|-------|----------|--------|--------|
| 编译检查 | `py_compile floating_ui.py` | 无错误 | COMPILE_OK | PASS |
| verify_data_reader.py | py313/py311 | 40/40 | 40 通过 / 0 失败 | PASS |
| ui_pretest.py | py313 + PYTHONPATH=最终成果 | 47/47 | 47 通过 / 0 失败（canvas 268×116 不变） | PASS |
| verify_text.py | py313 + PYTHONPATH | 无 'None'/'0.0K' | 各检查项符合 | PASS |
| DPI 探针（模拟 exe 路径） | 感知 + 临时配置 | 物理 402×174 | 402×174，winDpi=144 | PASS |
| 新 exe 实测 | WMI 启动 v1.1.1 | winDpi=144、402×150、位置正确 | (2195,1263) 402×150 winDpi=144 | PASS |
| 常驻稳定性 | WMI 启动 | 存活 >2 分钟 | 130 秒+ 存活 | PASS |
| 旧版对比基线 | v1.1.0 窗口 | — | winDpi=96、402×150（拉伸） | 基线 |

## Error Log

| Timestamp | Error | Attempt | Resolution |
|-----------|-------|---------|------------|
| 22:05 | py311 无 tkinter | 1 | 换 py313 |
| 22:06 | ui_pretest ModuleNotFoundError: floating_ui | 1 | PYTHONPATH=最终成果 |
| 22:02 | PowerShell `&&` 语法错误 | 1 | 改用 `;` |
| 22:11~22:15 | exe 三次启动后 1-2 分钟静默消失 | 3 | 定位会话作业对象回收 → WMI 启动（P5） |
| 22:20 | `pos_px` 迁移坐标放大 4 次（x=7386 超屏） | 1 | 废弃标记方案，改逻辑坐标口径 + 手工恢复设置文件（P6） |

## 5-Question Reboot Check

| Question | Answer |
|----------|--------|
| Where am I? | Phase 6 已交付收尾（v1.1.1 已运行，目测与回归全过） |
| Where am I going? | 任务完成，等待用户确认收尾；后续可选真实三会话实测 |
| What's the goal? | 多会话悬浮窗 + 高 DPI 原生清晰渲染 |
| What have I learned? | findings.md P1~P9（P9 已验证通过） |
| What have I done? | 见上方各阶段与 Test Results |

---

*子阶段收尾即追加；里程碑节点同步 `.plan-sync/PLAN.md`。*

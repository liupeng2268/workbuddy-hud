# Progress Log —— WorkBuddy 悬浮窗

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

- **Status:** in_progress
- 动作：
  - 排查 exe 启动后 1-2 分钟静默消失（3 次）：无日志、无崩溃事件、`%TEMP%` 残留 `_MEI*` → 会话作业对象回收，非缺陷（P5）
  - 改用 WMI `Win32_Process.Create` 脱离会话启动：稳定 130 秒+（PID 16712/17836）
  - 实测新窗口：`winDpi=144`、物理 402×150、位置 (2195,1263) = 逻辑 (1465,843)×1.4985（P8）
  - 更新 `.plan-sync/PLAN.md` 至 v1.1.1 里程碑
- 待办：用户目测文字清晰度与右键菜单位置（P9）

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
| Where am I? | Phase 6 交付与观察（v1.1.1 已运行） |
| Where am I going? | 用户目测确认 → （可选）右键菜单修正 / 三会话实测 / README 补表 |
| What's the goal? | 多会话悬浮窗 + 高 DPI 原生清晰渲染 |
| What have I learned? | findings.md P1~P9 |
| What have I done? | 见上方各阶段与 Test Results |

---

*子阶段收尾即追加；里程碑节点同步 `.plan-sync/PLAN.md`。*

# 任务计划：WorkBuddy 悬浮窗（多会话 + 高 DPI）

> 本文件是 planning-with-files 的过程台账，与 `.plan-sync/PLAN.md`（状态快照）配合使用：
> 台账记阶段与事实，快照记当前状态。写序：先台账、后快照。

## Goal

WorkBuddy 桌面悬浮窗按会话展示缓存命中率与生成速率（多会话并列、设置可切单会话），并在高 DPI 缩放屏上文字原生清晰渲染。

## Next Step

等待用户目测确认 v1.1.1：悬浮窗文字清晰度 + 右键菜单弹出位置是否在鼠标处（见 findings.md P9）。

## Current Phase

Phase 6（交付与观察）：v1.1.1 已发布运行，等待用户目测反馈。

## Phases

### Phase 1: 多会话展示实现

- [x] 每会话两行、上限 3 个 + 「+N 个会话」、10 分钟活跃窗口
- [x] 设置新增单/多会话开关，默认单会话
- **Status:** complete（2026-11-18 早间会话完成，随 v1.1.0 发布）

### Phase 2: 打包发布 v1.1.0

- [x] PyInstaller 打包并替换 dist exe，旧版备份
- **Status:** complete（2026-11-18 21:40）

### Phase 3: 排查「字体变模糊」

- [x] 实测当前窗口：winDpi=96、物理 402×150 = 268×1.5 → DPI 虚拟化位图拉伸
- [x] 对比新旧 exe manifest 一致、git 历史无 DPI 代码 → 实现缺口而非版本回归（findings.md P1/P2）
- **Status:** complete

### Phase 4: 高 DPI 代码修复

- [x] `run_app` 启用 System DPI 感知（门禁进程不经过 `run_app`，系数恒 1）
- [x] 全部固定像素常量按 `winfo_fpixels('1i')/96` 换算为实例属性
- [x] 位置记忆改「存逻辑坐标、用时乘回」口径（findings.md P6）
- **Status:** complete

### Phase 5: 门禁验证 + 打包 v1.1.1

- [x] verify_data_reader 40/40、ui_pretest 47/47、verify_text 通过
- [x] 端到端探针：DPI 感知进程内窗口 402×174、winDpi=144
- [x] 打包 v1.1.1、备份 v1.1.0、替换 dist exe
- **Status:** complete

### Phase 6: 交付与观察

- [ ] 用户目测：文字清晰度
- [ ] 用户目测：右键菜单位置（DPI 感知下 tk_popup 坐标待验证）
- [ ] 真实环境三会话同屏 + 溢出提示核对（低优先）
- **Status:** in_progress

## Key Questions

1. 为什么之前测试时清晰、现在模糊？→ 已答：150% 缩放下新旧版本同样模糊，之前清晰应是当时缩放/屏幕环境不同（findings.md P2）。
2. exe 启动 1-2 分钟后静默消失是什么 bug？→ 已答：不是缺陷，是代理会话作业对象回收子进程；WMI 脱离会话启动即稳定（findings.md P5）。

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| DPI 感知只在 `run_app` 启用 | 门禁直接实例化 `_FloatingWindow`，进程保持 96 DPI 虚拟化，断言与旧版一致 |
| 像素常量换成实例属性、模块常量保留 | 模块常量继续作为门禁的 96 DPI 基准 |
| 位置存 96 DPI 逻辑坐标 | 弃用 `pos_px` 标记迁移方案（多实例交叠下重复迁移 4 次，坐标爆到 7386,4251，见 findings.md P6） |
| 版本升至 1.1.1 | 用户确认打包并升版本 |

## Errors Encountered

| Error | Attempt | Resolution |
|-------|---------|------------|
| py311 无 tkinter，门禁跑不起来 | 1 | 改用 `D:\app\ai\py313\python.exe`（findings.md P7） |
| ui_pretest 找不到 floating_ui 模块 | 1 | 设 `PYTHONPATH=最终成果` |
| PowerShell 5.1 不支持 `&&` | 1 | 改用 `;` 分隔 |
| exe 启动后 1-2 分钟静默消失（3 次） | 3 | 排除崩溃/日志缺失/Defender 事件后定位为会话作业对象回收；改用 WMI `Win32_Process.Create` 启动 |
| `pos_px` 迁移标记丢失导致坐标重复放大 4 次 | 1 | 废弃标记方案，改存逻辑坐标口径（双向换算，天然幂等） |

## Notes

- 事实与证据集中在 findings.md（P1~P9），验证流水在 progress.md。
- 快照 `.plan-sync/PLAN.md` 每里程碑更新；本文件每阶段收尾更新。

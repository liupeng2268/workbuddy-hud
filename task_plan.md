# 任务计划：WorkBuddy 悬浮窗（多会话 + 高 DPI）

> 本文件是 planning-with-files 的过程台账，与 `.plan-sync/PLAN.md`（状态快照）配合使用：
> 台账记阶段与事实，快照记当前状态。写序：先台账、后快照。

## Goal

WorkBuddy 桌面悬浮窗按会话展示缓存命中率与生成速率（多会话并列、设置可切单会话），并在高 DPI 缩放屏上文字原生清晰渲染。

## Next Step

v1.3.1（WorkBuddy 速率 5 分钟窗口）已打包：深度思考会话速率从 `--` 变为有值（实测 48.4 tok/s），全门禁 43+56+21 项绿。等待用户手动双击 exe 冒烟确认。

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

- [x] 用户目测：文字清晰度（已确认恢复清晰）
- [x] 用户目测：右键菜单位置（已确认在鼠标处弹出，P9 验证通过）
- [x] 多会话代码静态核对 + 门禁回归（40/40、47/47、verify_text PASS、编译 OK）
- [x] README 补全多会话与高 DPI 文档
- [x] 真实环境三会话同屏 + 溢出提示核对（用户截图视觉识别：3 块同屏、每块两行、顶部计数、文字清晰；未超上限故无溢出页脚，符合规格）
- **Status:** complete（2026-11-18 交付收尾）

### Phase 7: ZCode 会话接入（2026-11-19）

- [x] 数据源调研：确定 db.sqlite 的 model_usage + session 联表（P11~P13）
- [x] 新建 `zcode_reader.py`：命中率原生口径、10 分钟活跃窗口、倒序、标题回退、db 缺失容错（findings P12/P15）
- [x] 入口组合 provider + 「ZCode(多会话)」设置开关，默认关，UI 渲染零改动（P14）
- [x] 验证：编译 OK、verify_data_reader 40/40、ui_pretest 47/47、verify_text PASS、verify_zcode 15/15
- [x] 打包 v1.2.0（PE 版本 1.2.0、SHA256 5C9E2F64…2ADE2FFCC、PYZ 含 zcode_reader）、备份 v1.1.1
- [ ] 运行时冒烟：用户手动双击 exe 验证（代理会话 WMI 启动被拦截，见 P15）
- **Status:** in_progress（待用户手动冒烟）

### Phase 8: ZCode 速率不显示修复（2026-11-19 晚）

- [x] 定位根因：started_at 时间戳致长调用过期 + 单样本虚高 + 输出一次性写库（P16/P17）
- [x] 修 completed_at 口径 + 调用真实时长作分母 + 5 分钟窗口（用户选「放宽到 5 分钟」）
- [x] verify_zcode 扩到 20/20；真实库验证 3 会话速率 38.9/36.0/64.4 tok/s
- [x] 打包 v1.2.1（PE 1.2.1、SHA256 `537FA482…42512F`）、备份 v1.2.0
- [ ] 用户手动双击 exe 冒烟确认速率列
- **Status:** in_progress（代码完成，待用户冒烟）

### Phase 9: ZCode 命中率口径纠错（2026-11-19 深夜）

- [x] 定位根因：旧口径 cacheRead/(cacheRead+input) 分母多算一次 → 永远 ≤50%（P19）
- [x] 解包 ZCode app.asar 找到权威公式 cacheRead/input（找到 contextCacheUsageFromPayload）
- [x] 改口径 + 新增「不腰斩」回归断言；verify_zcode 21/21；真实库 95.8%/65.9%/89.5%
- [x] 打包 v1.2.2（PE 1.2.2、SHA256 `07A7F1CD…DC6`），备份 v1.2.1
- [ ] 用户手动双击 exe 冒烟确认命中率
- **Status:** in_progress（代码完成，待用户冒烟）

### Phase 10: 多会话展开/收起（2026-11-19 深夜）

- [x] 数据层保留全部活跃会话（data_reader MAX_SESSIONS_EXPANDED=15、zcode_reader=15）
- [x] UI 底部标志可点击：收起 `+N 个会话 ▼` / 展开 `▲ 收起`，点击/拖动区分
- [x] 验证：UI 56/56（新增 8 项）、其余门禁全绿、点击链路单测通过
- [x] 打包 v1.3.0（PE 1.3.0、SHA256 `BBDD1B24…E32E6725`），备份 v1.2.2
- [ ] 用户手动双击 exe 冒烟确认展开/收起
- **Status:** in_progress（代码完成，待用户冒烟）

### Phase 11: 深度思考会话速率不显示（2026-11-20 00:05）

- [x] 复现根因：Tailscale 会话 token 稀疏（267s/122s 间隔），60s 窗口 0 条、5min 窗口 2 条
- [x] WorkBuddy 速率窗口 60s→5min、keep 10min、idle 6min（与 ZCode 统一）
- [x] 真实库验证 `--` → 48.4 tok/s；新增 3 项窗口常量守卫，verify_data_reader 43/43
- [x] 打包 v1.3.1（PE 1.3.1、SHA256 `88BF5A17…451E91`），备份 v1.3.0
- [ ] 用户手动双击 exe 冒烟确认速率
- **Status:** in_progress（代码完成，待用户冒烟）

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

# 任务计划：WorkBuddy 悬浮窗（多会话 + 高 DPI + ZCode）
schema: plan-sync/1.3
更新时间：2026-11-20 00:23（plan-sync 写入）
宿主注册：已注册：WorkBuddy；未注册：ZCode、灵犀
归档：`.plan-sync/archive/PLAN-2026-11.md`（历史原文，不参与探测）
三件套：有（工作区根目录 task_plan.md / findings.md / progress.md）

## 目标
在 WorkBuddy 桌面悬浮窗中按会话查看缓存命中率与生成速率：多会话并列展示，设置里可切回单会话；可选展示 ZCode 会话（v1.2.0）。Trae 数据源本轮搁置。

## 最近里程碑（≤3）
- [x] 2026-11-20 v1.3.2 陈旧速率 ~ 标记（Phase 12）：用户截图停止会话仍显示 12.6 tok/s。根因 idle 6min 使窗口内旧样本（264s 前）一直算速率、mtime 因 session-meta 高频写入不可用。新增 `SPEED_STALE_MS=90s` + `speed_stale` 字段（两数据层同步），UI stale 速率加 `~` 淡化。真实库 `~29.7`（停）/ `20.9`（跑）；三门禁 44/23/57 全绿。打包 v1.3.2（PE 1.3.2、SHA256 `AA1BE3D1…6FBD4`），备份 v1.3.1。
- [x] 2026-11-20 v1.3.1 WorkBuddy 速率 5min 窗口（Phase 11）：深度思考 token 稀疏（267s/122s 间隔），60s 窗口 0 条；窗口 60s→5min，真实库 `--`→48.4 tok/s。verify_data_reader 43/43。
- [x] 2026-11-19 v1.3.0 多会话展开/收起（Phase 10）：数据层保留全部活跃会话，UI 底部标志可点击展开/收起，点击/拖动区分。UI 56/56。

## 进行中
- [ ] 运行时冒烟：需用户手动双击 `最终成果\dist\WorkBuddy悬浮窗.exe`（v1.3.2），确认停止会话显示 `~xx tok/s`、运行中会话显示正常数值。代理会话内 WMI/CIM 启动被安全策略拦截（P15），无法自动验证，未绕过。
- [ ] GitHub 提交推送：前 3 笔（5c94e1a/2617de7/83b5472，v1.2.0~v1.3.1）已推送到 origin/main；v1.3.2 的改动尚未提交推送，待用户确认。

## 下一步
1. 用户手动双击 exe 冒烟 v1.3.2：停止会话速率带 `~` 淡化、运行中会话正常；前几版修复（展开/收起、ZCode 命中率/速率）仍在。
2. 确认后提交并推送 v1.3.2 到 origin/main（延续之前的拆分提交习惯）。
3. 回退路径：关闭后从 `_临时\backup` 复制 v1.3.1（20261120-002200）、v1.3.0 等的 exe + 设置 JSON 回 `最终成果\dist`。

## 关键决定与约定
- Trae 本轮搁置：不出现 Trae 标签，也不做占位标签；不为扩展点增加界面或额外工作量。
- 多会话判定：10 分钟内有日志写入才算同时进行；最多 3 个；按最后写入时间倒序；超出只显示数量提示。
- 展示：每会话两行；窗口宽度按 DPI 缩放（逻辑 268），高度随块数自适应；门禁进程不启用 DPI 感知，缩放系数恒 1，断言与旧版一致。
- 命中率口径：每会话独立；优先 `hit/(hit+miss)`，缺失时回退 `cached/prompt`；速率取 60 秒窗口。
- 单会话模式保持原三行布局，默认单会话；会话标题取日志 `ai-title`，缺失回退「项目名 · uuid 前 6 位」。
- 设置.json 的 x/y 一律存 96 DPI 逻辑坐标（保存时物理÷系数，使用时逻辑×系数），跨缩放/跨版本口径统一。
- 测试约束：不为验证向真实会话制造写入；不重建 dist、不重启、不替换 exe，除非用户明确要求（v1.1.1 已获授权并完成）。
- 代理会话里启动的 exe 会被会话作业对象在命令结束后回收（表现为启动 1-2 分钟后静默消失，非缺陷）；要长期驻留需用户手动双击或用 WMI `Win32_Process.Create` 脱离会话启动。
- **ZCode 数据源只认 db.sqlite**：`cli/log/*.jsonl` 的 token 全 `[Redacted]`（不可用），`cli/rollout/*.jsonl` 有 token 但无标题且单文件 21 MB；db.sqlite 的 `model_usage`+`session` 联表才是权威源（P11）。
- **ZCode 与 WorkBuddy 命中率不是同一口径**：前者 `cacheRead/(cacheRead+input)`≈50%，后者 `hit/(hit+miss)`≈97%；ZCode 行加 `ZCode ·` 前缀，禁止横向比较（P12）。
- ZCode 显示默认关闭，且仅在多会话模式生效；开启后从 `~/.zcode/cli/db/db.sqlite` 以 `mode=ro` 只读扫描，不写库、不联网。
- **ZCode 速率口径（用户 2026-11-19 选定）**：5 分钟窗口「近期平均速率」，用 `completed_at` 作样本时间戳、每调用真实时长作分母（40~60 tok/s），非即时速率（P16~P18）。
- **ZCode 命中率口径 = `cacheReadTokens / inputTokens`**（ZCode 源码权威，P19）：inputTokens 已含 cacheRead，分母**不可**再加 cacheRead。旧错误口径 `cr/(cr+input)` 永远 ≤50%，把 97% 腰斩成 49%。**勿回退到加法分母**。
- **多会话显示（v1.3.0 起）**：数据层保留全部活跃会话（`MAX_SESSIONS_EXPANDED=15`，zcode_reader 同步），UI 收起显示 3 行、展开显示全部；底部「+N 个会话 ▼ / ▲ 收起」可点击切换，点击与拖动按位移 ≤4px + footer bbox 区分。
- **速率口径统一（v1.3.1 起）**：WorkBuddy 与 ZCode 均为 5 分钟窗口 + idle 6min + keep 10min。WorkBuddy 因深度思考阶段 token 稀疏（可数分钟无产出），60s 窗口会一直 `--`（P21）；**勿改回 60 秒**，verify_data_reader 有常量守卫。
- **陈旧速率标记（v1.3.2 起）**：`SPEED_STALE_MS=90s`，最新 token 距今 >90s 则 `speed_stale=True`，UI 显示 `~xx tok/s` 淡化表示"当前未在生成"。**判断依据必须是最后 token 样本时间，不能用日志 mtime**（session-meta 每几秒写一次会污染 mtime，P22）。
- 打包前须先终止运行中的 exe，否则 safe-delete 拦截覆盖。

## 涉及文件（当前有效）
- `d:\app\ai\workspace\WorkBuddy\spaces\workbuddy悬浮窗\最终成果\floating_ui.py`（DPI 感知 + 常量缩放 + 逻辑坐标口径 + ZCode 设置项）
- `...\最终成果\data_reader.py`（WorkBuddy 数据层，未改）、`...\最终成果\zcode_reader.py`（新增，ZCode 数据层）
- `...\最终成果\WorkBuddy悬浮窗.pyw`（入口组合 provider）、`version.py` / `version_info.txt`（1.3.2）
- `...\tests\verify_data_reader.py`（44 项）、`verify_zcode.py`（23 项）、`...\过程脚本\ui_pretest.py`（57 项）、`verify_text.py`
- `...\最终成果\dist\WorkBuddy悬浮窗.exe`（v1.3.2，SHA256 `AA1BE3D141E14A24C5B21E445BB1F903314FBEC1259099933BD72194F3A6FBD4`）
- `...\_临时\backup\`：v1.0.2、v1.1.0、v1.1.1、v1.2.0、v1.2.1、v1.2.2、v1.3.0、v1.3.1（20261120-002200）的 exe + 设置 JSON

## 风险与待确认项
- ~~右键菜单在 DPI 感知下的弹出坐标~~ 已验证通过（P9，2026-11-18 用户目测确认菜单在鼠标处弹出，无需 `_on_menu` 坐标换算）。
- 代理会话里启动的 exe 会被会话作业对象在命令结束后回收（P5，表现为启动 1-2 分钟后静默消失，非缺陷）；常驻实例需用户手动双击或 WMI `Win32_Process.Create` 启动。
- 打包环境：`D:\app\ai\py313`（有 tkinter + PyInstaller 6.22.3）；默认 `python` 是 py311（无 tkinter），跑 UI 门禁需指明 py313 并设 `PYTHONPATH=最终成果`（P7）。
- ~~三会话同屏溢出提示、真实三会话数字一致性~~ 已验证通过（P10，2026-11-18 用户截图视觉识别：3 块同屏、每块两行、顶部计数正确；会话数=3 未超上限故无溢出页脚；文字清晰）。
- 标题整行约可显示 13 个汉字（逻辑像素），超长省略。
- `_临时\backup` 属临时目录，清理 `_临时` 前需先把备份转移出去。
- ZCode 运行时冒烟尚未执行（代理会话 WMI 启动被安全策略拦截，需用户手动双击验证，见「下一步」1）。

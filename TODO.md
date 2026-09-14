# TODO

> 由 `bili-summary` 的计划讨论整理（记录时应为项目首次建立 git 仓库时）。
> 优先级：**P0 先做** / P1 次之 / P2 有余力再说。
> 每项包含：**为什么** / **改哪里** / **完成标准**。

---

## 一、内容质量

### P0 · 噪声治理（把过程性内容从所有产物里滤掉）
- **为什么**：讲稿（`lecture.md`）已经通过"重写成连贯文章"自然滤掉了调试/闲聊，但
  `report.html` / `notes.md` 还是照单全收 —— 例如逐页笔记里的
  `口头补充：讲者在反复调试 PowerPoint 的"设置放映方式"对话框…`。
  结构化报告可以碎片化，但不该把**与课程无关**的内容当正文。
- **改哪里**：
  - `describe/fuse.py` 的 `INSTRUCTION`：给每页加 `content_type: content|process|off_topic`
    与 `keep: bool`，并明确列出要排除的过程性内容；`speech_only` 的定义收紧为
    "只记录对理解本页技术内容有增量的部分"。
  - `assemble/report.py` + `assemble/html.py`：`keep=false` 的页不进正文，
    **折叠到文末"过程记录"区块**（不删除，保留可追溯性）。
  - 规则兜底（可选）：命中「调试/放映/演示者视图/共享屏幕/听得到吗」且时长 < 30s 的段降权。
- **完成标准**：Tensor Core 那门课（开场 1 分 20 秒都在调放映设置）跑完后，
  `notes.md` / `report.html` 正文里不再出现"调试放映设置"这类内容；
  这些内容可在"过程记录"里查到。

### P0 · 切分优化：PPT 编辑模式检测 + 同页合并调优
- **为什么**：
  1. 偶尔抓到 **PPT 编辑模式**（左侧缩略图栏 + 顶部功能区）的帧，画面对 M3 是干扰；
  2. 同页合并目前靠"图像像素差找候选 + M3 确认"，阈值 0.08，仍有轻微过切/漏合
     （实测同一页 0.000、带批注 0.011、不同页 ≥0.059）。
- **改哪里**：
  - `segment/sampler.py` / `segment/detector.py`：加"编辑态"判据
    （左侧固定宽度的缩略图栏具有周期性竖向结构），命中则改用上一稳定帧或裁剪该区域。
  - `describe/fuse.py`：`dedup_slides` 的候选阈值改为**自适应**（按当次相似度分布定），
    并让 M3 一次性看"候选团"而不是两两确认，减少调用数。
- **完成标准**：
  - `segment eval` 的切点对照图上不再出现编辑模式帧；
  - `dedup_slides` 后的"唯一页数"与人工抽查一致（误差 ≤ 2 页）。

---

## 二、覆盖能力

### P1 · 支持 YouTube 及其他平台
- **为什么**：现在只有 B站（wbi 签名 + SESSDATA 拿官方字幕）。YouTube 有成熟的
  `youtube-transcript-api` / yt-dlp 字幕，接入成本低。
- **改哪里**：新增 `acquire/youtube.py`（字幕优先 → 无字幕走 ASR），
  `acquire/download.py` 已有 yt-dlp 通道；`pipeline.build_transcript` 按域名分发。
- **完成标准**：`bili-summary run <YouTube URL>` 能跑通全流程并产出讲义。

### P1 · 批量处理整个系列 / 合集
- **为什么**：CS329A 有 19 个分P，系列讲座有多个讲。现在一次只能跑一个视频。
- **改哪里**：
  - `acquire/bilibili.py` 已有 `pages` 列表，暴露 `--all-pages`；
  - `pipeline.py` 加 `run-series`：按分P/播放列表逐个跑，最后生成**合订讲义**
    （跨视频的目录 + 术语表 + 统一编号）。
  - `workspace.py` 加 `series/` 布局（每个视频一个子工作目录 + 一份合并产物）。
- **完成标准**：一条命令跑完 CS329A 全 19 讲，输出一份合订 `lecture.html`。

### P1 · 非 PPT 视频的降级策略
- **为什么**：纯敲代码演示 / 纯口播（无幻灯片）时，逐帧差分会把代码滚动切成碎片。
- **改哪里**：`segment/detector.py` 已有 `dynamic` 段雏形；补齐"整段无稳定帧 →
  退化为按时间固定切分"的路径，并给这些段打 `kind=dynamic`，
  在 S4 提示里告知"本段没有幻灯片可依赖，按讲述内容整理"。
- **完成标准**：拿一段纯终端演示视频跑，切出的段数在合理范围（约每 60–90s 一段），
  讲义里不出现"幻灯片/这一页"这类描述。

### P2 · 多语言支持
- **为什么**：英文课程 / 中英混讲。
- **改哪里**：`acquire/asr.py`（language 自动检测、initial_prompt 按语言切换）、
  `describe/*` 与 `teach/*` 的 prompt 增加 `output_language` 配置。
- **完成标准**：英文课程产出的讲义为英文（或中英对照），术语不乱译。

---

## 三、学习效果

### P1 · 交互式自测（HTML 可作答、自动判分）
- **为什么**：现在 `quiz.md` 是静态"题目 + 答案"，学员容易直接翻答案。
- **改哪里**：`assemble/html.py` 增加一个轻量内联 JS：
  选择题可点选、即时判分、显示解析；简答题给"参考答案"折叠。
  `quiz.json` 已有结构化数据（type/options/answer/explanation/slide）。
- **完成标准**：`lecture.html` / `report.html` 里的测验可点击作答并给出得分。

### P1 · 学习进度追踪
- **为什么**：闪卡答对/答错没有记录，无法动态调整复习节奏。
- **改哪里**：在交互式自测里用 `localStorage` 记录每题/每卡的历史；
  页面上显示"本轮得分 / 历史正确率 / 需要重刷的条目"。
- **完成标准**：同一份 HTML 重复打开能累计进度，并高亮"上次做错的题"。

### P2 · RAG 问答（对讲义提问）
- **为什么**：讲义是长文，想快速定位"某概念在第几页讲过"。
- **改哪里**：新增 `qa/`：用 `sqlite` + 向量（或 FTS5）索引 `slides.json` +
  `lecture.md` 分段；CLI 加 `ask` 子命令；回答带**幻灯片编号与时间戳**引用。
- **完成标准**：`bili-summary ask --workdir W "wgmma 和 mma.sync 的区别"` 返回带引用的答案。

---

## 四、工程交付

### P1 · MCP Server / Agent Skill
- **为什么**：这是项目最初的形态诉求 —— 让 Claude Code / OpenCode 这类 agent
  直接调用，而不是人来敲命令。
- **改哪里**：新增 `mcp_server.py`，把关键动作暴露为工具：
  `run_video` / `get_status` / `read_lecture` / `ask` / `list_workdirs`。
  任务用后台线程 + 状态查询（参考 VideoNote-MCP 的状态机设计）。
- **完成标准**：agent 能"给一个链接 → 轮询进度 → 拿到讲义路径并总结"。

### P2 · Web UI
- **为什么**：粘贴链接、看进度、浏览结果，不用碰命令行。
- **改哪里**：`FastAPI` + 单页前端复用现有 `assemble/html.py` 产物；
  用 SSE 推进度（各阶段已有明确的 print 日志点，可结构化）。
- **完成标准**：浏览器里粘贴链接 → 看到阶段进度 → 直接阅读讲义。

---

## 五、待确认 / 杂项
- [ ] **License**：仓库已按 MIT 建立（`LICENSE`），如需更改请说明。
- [ ] **轮换 MiniMax API key**：key 曾出现在聊天记录中，建议到控制台重置。
- [ ] `libs/`（cuBLAS12 + cuDNN9，1.3GB）未入库；新克隆需按 README 说明补齐。
- [ ] `assets/mathjax-tex-svg.js`（2.1MB，Apache-2.0）已入库以保证 HTML 离线可用。

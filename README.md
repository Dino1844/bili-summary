# bili-summary

把**课程视频**按 **PPT 翻页**切分，逐页对齐讲稿，再用多模态大模型生成
**可替代看视频的复习笔记 + 一整套复习方案**。

一条命令，粘贴链接即可：

```bash
cd ~/bili_summary
uv sync
uv run bili-summary run "https://www.bilibili.com/video/BV1g83w6DETm"
```

产物全部落在 `work/<视频ID>/`（代码与数据分离）。

---

## 为什么不用现成方案

调研了 BiliNote / VideoNote-MCP / bilibili-video-notes-skill / notewise / lectureflow /
NoteTaker-py 六个项目，它们**全部采用"固定间隔抽帧"**（6s / 10s 一张），
**没有一家做幻灯片边界切分** —— 因此无法把讲稿与"当前这一页"精确对齐。

本项目以 **PPT 翻页作为语义单元**：这是课程视频最自然的边界，也是做到"逐页对齐讲稿"的前提。

---

## 流水线

```
输入(B站/YouTube 链接, 或本地视频)
  │
  ├─ S1 采集   acquire/   下载(yt-dlp) → 平台官方字幕优先(B站 wbi API + SESSDATA)
  │                       → 无字幕则 faster-whisper ASR(词级时间戳)
  │
  ├─ S2 切分   segment/   流式 RGB 度量 → 三层差分分类 → 稳定段切分 → 去重 → 关键帧
  │
  ├─ S3 装配   enrich/    按段时间轴切讲稿 → materials.json
  │
  ├─ S4 笔记   describe/  MiniMax M3 逐窗口融合(同页合并 + 逐页结构化笔记)
  │                       → 跨窗口 + 基于图像的全局去重
  │
  ├─ S5 教学   teach/     学习目标(布鲁姆) / 闪卡 / 测验 / 思维导图 / 间隔重复计划
  ├─ S5.1 讲稿 teach/     把逐页笔记重写成**一篇连贯讲稿**, 自动滤掉过程性噪音
  │
  └─ S6 报告   assemble/  report.html(正文=讲稿 + 可折叠附录 + 内嵌图) + notes.md
```

### 为什么需要"讲稿"
逐页笔记是"按幻灯片切块"的清单，且会**忠实保留讲者的过程性内容**（调试设备、切换窗口、
调整放映设置、打招呼闲聊），因为 prompt 只是"如实描述这一页"。
一旦要求"写成一节连贯的讲稿"，模型必须自己决定什么值得讲 —— **过程性内容自然被滤掉**，
同时把题目 / 知识点 / 闪卡融进同一篇文档，图片按位置引用。

## 产出目录（自动生成 `README.md` 索引）

```
work/<视频ID>/
├── README.md     自动生成的索引, 说明每个文件是什么
├── final/        ⭐ **只需要看这里**
│   ├── lecture.html     ⭐ **独立讲义**：连贯文章 + 幻灯片图 + 全部资料附录
│   ├── lecture.md       讲义 Markdown 源
│   ├── report.html      结构化报告：逐页笔记(含图)
│   ├── notes.md         逐页笔记(结构化)
│   ├── learning_pack.md 学习目标 + 费曼提示 + 知识关联
│   ├── flashcards.md    闪卡(主动回忆)
│   ├── quiz.md          提取练习测验(含答案解析)
│   ├── mindmap.mmd      思维导图(Mermaid)
│   └── study_plan.md    间隔重复复习计划
├── data/         结构化 JSON(transcript/segments/materials/slides/flashcards/quiz/usage)
├── cache/        中间产物(metrics.npz / keyframes / materials / fuse / cuts), 可删可重算
└── source/       原始媒体(video.mp4 / info.json / audio.wav)
```

所有路径只在 `workspace.py` 里定义一次；旧版平铺目录会在首次运行时**自动迁移**到该布局。

## CLI

```bash
# 全流程（推荐）
bili-summary run <链接|本地文件> [--workdir W] [--window 6] [--limit N]
                                  [--asr-model small] [--device cuda]
                                  [--crop/--no-crop] [--no-teach] [--from <阶段>]

# 分阶段
bili-summary acquire subs BV1xxx [--fetch -o subs.json]   # 探测/抓取 B站官方字幕
bili-summary acquire asr --video V --out tr.json [--audio a.wav]
bili-summary segment run V --workdir W [--metrics W/metrics.npz]
bili-summary segment stats V --metrics W/metrics.npz      # 阈值敏感性诊断
bili-summary segment eval --video V --workdir W --n 16    # 切点对照图, 人工核验
bili-summary enrich  --workdir W [--crop]
# 全流程（推荐）
bili-summary run <链接|本地文件> [--workdir W] [--window 6] [--limit N] [--jobs 4]
                                  [--asr-model small] [--device cuda]
                                  [--crop/--no-crop] [--no-teach] [--from <阶段>]

# 分阶段
bili-summary acquire subs BV1xxx [--fetch -o subs.json]   # 探测/抓取 B站官方字幕
bili-summary acquire asr --video V --out tr.json [--audio a.wav]
bili-summary segment run V --workdir W [--metrics M]      # S2 切分
bili-summary segment stats V --metrics W/cache/metrics.npz  # 阈值敏感性诊断
bili-summary segment eval --video V --workdir W --n 16    # 切点对照图, 人工核验
bili-summary enrich  --workdir W [--crop]                 # S3 素材
bili-summary describe --workdir W [--window 6] [--jobs 4] # S4 逐页笔记
bili-summary dedup   --workdir W [--threshold 0.08]       # 按图像全局去重(M3 确认)
bili-summary teach   --workdir W [--jobs 3]               # S5 教学包
bili-summary lecture --workdir W [--jobs 4] [--sections N] # S5.1 讲义
bili-summary render  --workdir W [--math inline|cdn|none] # ★ 不调 M3: 重渲染 HTML(改样式/修公式)
bili-summary report  --workdir W [--title T] [--url U]    # S6 报告
bili-summary usage   --workdir W                          # M3 token 用量
bili-summary sheet   --workdir W --source materials       # 幻灯片总览图 -> final/

# 断点续跑：所有阶段按产物存在性跳过；--from <阶段> 可强制从某阶段重跑
```

**并行**：不同 M3 窗口 / 去重确认 / 教学包三路相互独立，`--jobs` 控制并发数（默认 4）。
**token 统计**：按阶段累计写入 `data/usage.json`，`run` 结束会打印用量表。
**公式渲染**：HTML 内嵌 MathJax（**SVG 输出，不需要字体文件**，单文件离线可用）。
LaTeX 会在进 markdown 前被抽出保护（避免 `\sqrt` / `\{` / `\_` 被转义），
再以 `\(...\)` / `\[...\]` 形式还原；正文里"不属于公式的裸 `$`"一律转成 `&#36;`，
所以 MathJax 永远不会把 `$ 这句话 $` 误判成公式。
**零成本重渲染**：`bili-summary render --workdir W` 只重跑 HTML 渲染（不调用 M3），
改样式、修渲染问题都用它。

---

## S2 切分：三个关键设计（都是踩坑换来的）

**1. 三层差分分类**（单位：块均值灰度绝对差）

| 幅度 | 判定 | 依据 |
| --- | --- | --- |
| `< 2.5` | 动画 / 鼠标 / 高亮 → 同一页 | 实测真实翻页几乎不低于 2.5 |
| `2.5 ~ 25` | **幻灯片翻页** | 实测真实翻页集中在 3.7–14 |
| `> 25` | 切应用 / 放视频 / 换窗口 | 实测窗口切换在 30–185 |

**2. 不要平滑**：翻页在 mad 上是**1 秒脉冲**，中值滤波会抹平它
（实测 p95 从 3.24 掉到 0.23，直接漏光）。

**3. 红笔批注剔除**：中文课程讲者常在 PPT 上画红笔。采样保留 RGB，
把"新增的彩色像素"从结构性变化中扣除，避免批注被误判为翻页。

其他要点：
- **关键帧取"段末内容最丰富的稳定帧"**：取首帧丢分步动画，取末帧会抓到淡出黑屏。
- **阈值用 `segment stats` 的分布来定**。`area_min` 最敏感：白底稀疏页翻页只改 <12% 像素。
- **默认不裁剪**：任何自动裁剪都有切掉幻灯片内容的风险（实测会切掉左右边缘文字），
  而 chrome（菜单栏/水印）不影响理解。需要时用 `--crop`。

### 已知局限
- **"相似页" vs "同一页"**在低分辨率灰度下分不开 → **S2 故意保持高召回**，
  把判断交给 **S4 的 M3 看全分辨率原图**（同页合并）。
- 全局去重分两步：**图像像素差找候选对**（实测：同一页 0.000、带批注 0.011、
  不同页 ≥0.059，阈值 0.08 可干净分开）→ **M3 看两张原图确认**。
  比只给标题文本可靠得多（标题是模型生成的，同一页可能被起两个名字）。
- 偶尔会抓到 **PPT 编辑模式**（带左侧缩略图栏）的帧，尚未专门处理。

---

## 环境

### 密钥
`~/bili_summary/.env`（`chmod 600`，已在 `.gitignore`）：
```
MINIMAX_API_KEY=...                        # MiniMax M3, Anthropic 兼容协议
MINIMAX_ANTHROPIC_BASE_URL=https://api.minimaxi.com/anthropic
MINIMAX_MODEL=MiniMax-M3
BILI_SESSDATA=...                          # B站登录态, 用于抓官方 AI 字幕
```

### 模型选择
- **幻灯片理解直接用 M3**（实测能准确转录幻灯片公式/代码），不必另接 Gemini/GPT-4o。
- **协议用 Anthropic 兼容**：thinking 独立成块、prompt cache 生效；
  OpenAI 协议会把 `<think>` 混进正文。

### CUDA（faster-whisper）
ctranslate2 需要 **cuBLAS 12** + **cuDNN 9**，而系统 `cuda/13.2` 只提供 cuBLAS 13。
`libs/` 里自带了这两个库；`config.ensure_cuda_env()` 会**自动把它们加进
`LD_LIBRARY_PATH` 并重新 exec 自身**——所以用户不需要 `module load`，
也不需要任何包装脚本，直接敲 `bili-summary` 就能用 GPU。

（原理：glibc 只在进程启动时读 `LD_LIBRARY_PATH`，所以在 `cli.main()` 里判断后 re-exec。）

### 网络
- **B站 API 必须直连**：httpx 默认走 `HTTPS_PROXY`（境外出口）会被 412 风控，
  代码中已用 `trust_env=False` 绕过。
- **MiniMax 也默认直连**：它是国内端点，走本机代理（如 `127.0.0.1:7891`）时
  **并发请求会被掐断**，报 `UNEXPECTED_EOF_WHILE_READING`（TLS 被中途关闭）。
  需要走代理时设 `MINIMAX_USE_PROXY=1`。实测直连比走代理快约一倍。
- **瞬时网络错误自动重试**：连接被重置 / 超时 / 5xx / 429 都会按**指数退避 + 抖动**重试
  （最多 6 次，单次请求内部），窗口级再重试一次；每次重试都会**新建连接**，
  避免复用已被对端关闭的连接。
- **失败会明确报告**：某个窗口彻底失败时打印
  `[warn] N 个窗口失败, 这些内容会缺失`，直接重跑 `bili-summary describe --workdir W`
  只会补跑失败的窗口，已完成的不重复调用。
- faster-whisper 若模型缺失会卡在代理上下载 HuggingFace → `asr.py` 使用 HF 缓存，
  缺失时明确报错而不是静默挂起。

---

## 目录

```
~/bili_summary/
├── src/bili_summary/
│   ├── cli.py          # 唯一入口
│   ├── config.py       # .env / CUDA 环境(自动 re-exec)
│   ├── workspace.py    # ★ 工作目录布局与路径解析(唯一来源)
│   ├── pipeline.py     # run + 各阶段命令
│   ├── acquire/        # S1  下载 / 字幕 / ASR / 统一 Transcript
│   ├── segment/        # S2  采样 / 切分 / 关键帧 / 评估
│   ├── enrich/         # S3  素材装配
│   ├── describe/       # S4  M3 客户端 / 窗口融合 / 全局去重 / token 统计
│   ├── teach/          # S5  教学包 + 讲稿
│   └── assemble/       # S6  notes.md / report.html
├── libs/               # cuBLAS12 + cuDNN9 (gitignore)
├── legacy/             # 早期一次性脚本(保留备查)
└── work/<id>/          # 见上面「产出目录」(gitignore)
```

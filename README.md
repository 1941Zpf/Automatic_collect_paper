# arXiv CV 每日自动报告

这个脚本会自动生成每日报告，核心能力：

- 仅抓取 `cs.CV`
- 默认使用 `recent/list` 稳定抓取，尽量规避 arXiv API `429`
- 失败时自动回退并使用最近一次成功快照，避免生成空报告
- 对 `recent/list` 的前 N 篇补抓 `abs` 页摘要、备注和期刊信息，提升分类与中文摘要质量
- 中文化会先复用缓存，再对缺失项做批量补译，避免报告里残留 `[未翻译]`
- 提取 `comment` / `journal_ref` 里的中稿线索（CVPR/ICCV/ECCV/NeurIPS/ICLR/TPAMI 等）
- 基于标题+摘要做领域/任务/类型标签
- 生成 HTML 报告与 JSON
- 报告仅保留概览卡片、中文摘要、arXiv/PDF 链接和“中稿线索总表”
- “中稿线索总表”包含中文标题、中文摘要，并按动态会刊分组展示
- HTML 报告新增“论文主题探索器”独立分区：参考 arXiv Sanity 的探索思路，提供主题索引、主题/论文搜索、综合/Focus/中稿排序、局部关系图和代表论文面板
- 支持通过入口切换 `CV/AI` 域，并自定义 Focus 主题词
- 额外生成 Focus 池（tracking / 多模态融合 / TTA / domain shift / prompt tuning / distribution shift / online adaptation 等）
- LLM 翻译支持失败自动重试 + 断点续跑缓存（`data/llm_translation_cache.json`）

## 运行

交互式入口（推荐日常使用）：

```bash
./digest_wizard.sh
```

它会提供菜单，让你选择：

- 使用 `.env.digest` 默认配置直接抓取
- 选择预设：`cv` / `ai` / `both` / `tracking` / `quick`
- 自定义本次抓取：领域、Focus 关键词、抓取数量、翻译后端、摘要补抓数量等
- 在真正执行前可选填写“报告文件后缀”，留空则保持默认文件名
- 自定义模式完成后可选择是否保存到 `.env.digest`，默认不保存
- 使用默认配置运行时，会额外提示是否忽略当前配置下已经抓取过的论文，默认忽略
- 预览命令但不执行
- 查看或打开最新 HTML 报告

也可以用非交互方式预览：

```bash
./digest_wizard.sh --dry-run --preset tracking
./digest_wizard.sh --dry-run --default
```

底层直接运行：

```bash
./run_daily_digest.sh
```

常用临时参数：

```bash
./run_daily_digest.sh --date 2026-03-27
./run_daily_digest.sh --domain ai
./run_daily_digest.sh --focus-terms "agent,reasoning,alignment,tool use"
./run_daily_digest.sh --focus-terms-extra "video reasoning,test-time inference"
./run_daily_digest.sh --ignore-fetched 1
./run_daily_digest.sh --output-suffix tracking_debug
./run_daily_digest.sh --daily-limit-per-cat 260
./run_daily_digest.sh --abs-enrich-limit -1 --report-abs-enrich-limit -1
./run_daily_digest.sh --focus-latest 100 --focus-hot 0 --venue-latest 0 --venue-watch-limit 100
./run_daily_digest.sh --llm-max-retries 4
./run_daily_digest.sh --model moonshot-v1-8k
```

## 输出文件

- `reports/arxiv_digest_YYYY-MM-DD.html`
- `reports/arxiv_digest_latest.html`
- `reports/arxiv_digest_YYYY-MM-DD.md`
- `data/arxiv_digest_YYYY-MM-DD.json`

## 研究工作台（新增，不影响日报主流程）

如果你想在现有系统之外，额外生成：

- 最近 1-3 个月 `test-time adaptation` 方向论文包
- 最近 1-2 天整个 `cs.CV` 论文包
- 一套给 ChatGPT / 桌面版 ChatGPT 直接使用的提示词与研究语料

可以运行：

```bash
./run_research_workbench.sh
```

常见用法：

```bash
./run_research_workbench.sh --tta-days 90 --cv-recent-days 2
./run_research_workbench.sh --tta-days 60 --cv-recent-days 1 --output-suffix quick_scan
./run_research_workbench.sh --tta-days 90 --cv-recent-days 2 --translate-backend google
./run_research_workbench.sh --tta-days 90 --cv-recent-days 2 --tta-threshold 7
```

这个模块会复用现有 arXiv 抓取能力，但输出到独立目录，不会覆盖日报 HTML / JSON：

- 默认 `RESEARCH_TTA_THRESHOLD=7`，会比更宽松的阈值更少混入边界论文；如果你想把 `source-free UDA` 之类相邻方向也尽量收入，可以手动降到 `6`

- `reports/research_workbench/<packet>/quickstart.md`
- `reports/research_workbench/<packet>/project_instructions.md`
- `reports/research_workbench/<packet>/prompt_tta_landscape.md`
- `reports/research_workbench/<packet>/prompt_cross_ideation.md`
- `reports/research_workbench/<packet>/tta_corpus.md`
- `reports/research_workbench/<packet>/cv_recent_corpus.md`
- `reports/research_workbench/<packet>/tta_landscape_brief.md`
- `reports/research_workbench/<packet>/cross_ideation_seeds.md`
- `data/research_workbench/<packet>/tta_papers.json`
- `data/research_workbench/<packet>/tta_papers.csv`
- `data/research_workbench/<packet>/cv_recent_papers.json`
- `data/research_workbench/<packet>/cv_recent_papers.csv`
- `data/research_workbench/<packet>/packet_manifest.json`

## 不用 API，如何配合 ChatGPT 使用

这个项目现在采用的是“本地自动准备语料 + ChatGPT 做分析”的工作流：

1. 本地脚本自动完成：
   - 抓取 TTA 语料
   - 抓取近 1-2 天 CV 语料
   - 做启发式标签、假设信号、方法路线、脆弱点整理
   - 自动生成 ChatGPT Project Instructions 和两套提示词
2. 你在 ChatGPT 网页版或桌面版里手动完成最后一步：
   - 新建一个 Project
   - 把 `project_instructions.md` 内容贴到 Project Instructions
   - 上传 `tta_corpus.md`、`tta_landscape_brief.md`、`cv_recent_corpus.md`、`cross_ideation_seeds.md`
   - 先贴 `prompt_tta_landscape.md`
   - 再贴 `prompt_cross_ideation.md`

推荐直接先看：

- `reports/research_workbench/<packet>/quickstart.md`

这样做的好处是：

- 不需要 OpenAI API Key
- 不占你这套抓取系统的 API 预算
- 现有日报系统和研究工作台彼此隔离
- 生成的语料包、提示词、分析结果都可以版本化保存

## 本地大模型全自动模式

如果你已经有一个本地 OpenAI-compatible 服务，例如：

```bash
LOCAL_LLM_API_BASE="http://localhost:8080"
```

可以直接运行全自动链路：

```bash
./run_research_autopilot.sh
```

如果你只想复用已有 packet 做本地模型推理，不想重新抓取：

```bash
RESEARCH_SKIP_FETCH=1 ./run_research_autopilot.sh
```

如果你想明确指定复用哪一个历史 packet：

```bash
RESEARCH_SKIP_FETCH=1 \
RESEARCH_REUSE_PACKET="2026-04-10_tta90d_cv2d" \
./run_research_autopilot.sh
```

它会在完成抓取和语料整理后，继续自动做三件事：

1. 用本地模型生成第一轮 `TTA landscape analysis`
2. 用本地模型对第一轮结果做结构化审查，输出 JSON reviewer 意见
3. 把 reviewer 意见回灌到第二轮 prompt，生成 refined 版本

同样会自动生成 `cross-ideation` 的初稿、审查和 refined 版本。

默认环境变量：

```bash
LOCAL_LLM_API_BASE="http://localhost:8080"
LOCAL_LLM_MODEL="auto"
RESEARCH_ANALYSIS_BACKEND="local"
RESEARCH_SKIP_FETCH="0"
RESEARCH_REUSE_PACKET=""
RESEARCH_ANALYSIS_MAX_TTA_RECORDS="80"
RESEARCH_ANALYSIS_CV_HIGHLIGHT_LIMIT="40"
RESEARCH_ANALYSIS_MAX_OUTPUT_TOKENS="2400"
RESEARCH_ANALYSIS_REVIEW_OUTPUT_TOKENS="900"
```

如果 `/v1/models` 自动探测模型失败，你可以手动指定：

```bash
LOCAL_LLM_MODEL="your-local-model-name"
./run_research_autopilot.sh
```

自动分析产物会额外写到：

- `reports/research_workbench/<packet>/auto_analysis/tta_landscape_analysis_round1.md`
- `reports/research_workbench/<packet>/auto_analysis/tta_landscape_analysis_review.json`
- `reports/research_workbench/<packet>/auto_analysis/tta_landscape_analysis_final.md`
- `reports/research_workbench/<packet>/auto_analysis/cross_ideation_analysis_round1.md`
- `reports/research_workbench/<packet>/auto_analysis/cross_ideation_analysis_review.json`
- `reports/research_workbench/<packet>/auto_analysis/cross_ideation_analysis_final.md`
- `reports/research_workbench/<packet>/auto_analysis/auto_analysis_summary.md`
- `data/research_workbench/<packet>/auto_analysis_manifest.json`

本地模型接口默认按 OpenAI-compatible 方式访问：

- `GET /v1/models`
- `POST /v1/chat/completions`
- 若支持，也会优先尝试 `POST /v1/responses`

## 中文摘要与中文标题

默认 `TRANSLATE_BACKEND=google`：使用 Google Translate 免费翻译，不调用 LLM API。

如果希望优先使用 Kimi/LLM，再用 Google Translate 兜底，可以切换为：

```bash
./run_daily_digest.sh --translate-backend auto
```

配置 `OPENAI_API_KEY` 后会生成高质量中文标题和中文摘要：

```bash
export OPENAI_API_KEY="<YOUR_KEY>"
export OPENAI_MODEL="gpt-5-mini"
./run_daily_digest.sh
```

未配置 Key 时，脚本会用回退文案占位（不会中断流程）。

使用 Kimi（Moonshot）时可直接配置：

```bash
export KIMI_API_KEY="<YOUR_KIMI_KEY>"
export KIMI_API_BASE="https://api.moonshot.cn/v1"
export KIMI_MODEL="moonshot-v1-8k"
./run_daily_digest.sh
```

推荐把密钥写到项目根目录 `.env.digest`（便于 cron 非交互运行）：

```bash
KIMI_API_KEY="<YOUR_KIMI_KEY>"
KIMI_API_BASE="https://api.moonshot.cn/v1"
KIMI_MODEL="moonshot-v1-8k"
```

## 关键环境变量

- `DIGEST_TZ`：默认 `Asia/Shanghai`
- `DIGEST_DOMAIN`：默认 `cv`，可切换为 `ai` 或 `both`
- `ARXIV_CATEGORIES`：留空时自动由 `DIGEST_DOMAIN` 推导
- `ARXIV_MODE`：默认 `recent_only`
- `DAILY_LIMIT_PER_CAT`：默认 `260`（latest 补抓）
- `ARXIV_PAGE_SIZE`：默认 `200`
- `ARXIV_MAX_SCAN`：默认 `5000`
- `FOCUS_LATEST_N`：默认 `100`
- `FOCUS_HOT_N`：默认 `0`（禁用）
- `FOCUS_API_ENABLE`：默认 `0`（禁用 arXiv Focus API）
- `FOCUS_RECENT_SCAN`：默认 `1200`
- `FOCUS_TERMS_OVERRIDE`：留空时使用默认 Focus 主题；填写后完全替换
- `FOCUS_TERMS_EXTRA`：在默认主题后追加额外 Focus 关键词
- `VENUE_LATEST_N`：默认 `0`（禁用）
- `VENUE_WATCH_LIMIT`：默认 `100`
- `ABS_ENRICH_LIMIT`：默认 `-1`（对日报全部论文补抓 abs 页面；设为正数时只补前 N 篇）
- `FOCUS_ABS_ENRICH_LIMIT`：默认 `0`（可选开启，对 Focus 扩展池前 N 篇补抓 abs 页面）
- `REPORT_ABS_ENRICH_LIMIT`：默认 `-1`（翻译前对报告内仍缺摘要的论文补抓 abs 页面，避免用标题误当摘要）
- `TRANSLATE_MODEL`：默认 `moonshot-v1-8k`（轻量翻译模型）
- `TRANSLATE_BACKEND`：默认 `google`，可选 `llm` / `google` / `auto`
- `IGNORE_FETCHED_ARTICLES`：默认 `1`。同一抓取配置下会自动忽略已经抓取过的论文，只保留新的论文
- `LLM_LIMIT`：默认 `-1`（对全部论文尝试中文化；设为 `0` 可禁用本次 LLM 调用）
- `LLM_MAX_RETRIES`：默认 `2`
- `LLM_FAILED_COOLDOWN_HOURS`：默认 `24`（失败条目在冷却时间内不重复重试）
- `LLM_TIMEOUT_SECONDS`：默认 `25`
- `GOOGLE_TRANSLATE_TIMEOUT_SECONDS`：默认 `12`
- `GOOGLE_TRANSLATE_LIMIT`：默认 `-1`（Google 模式下翻译全部缺失项；调试时可设为小数字）
- `GOOGLE_SUMMARY_SENTENCES`：默认 `3`（Google 模式下会先从完整英文摘要中抽取 2-3 句“问题/方法/结果”要点，再翻译成中文）
- `GOOGLE_TRANSLATE_FULL_ABSTRACT`：默认 `0`，设为 `1` 时翻译完整摘要
- `OPENAI_API_KEY`：用于中文翻译与中文摘要
- `OPENAI_MODEL`：默认 `gpt-5-mini`

## 已抓取记录

- 脚本会根据当前抓取配置自动生成独立的已抓取状态文件，默认放在：
  - `data/fetch_state/`
- 配置签名会综合考虑：
  - 领域
  - arXiv 分类
  - 抓取模式
  - 日报 / Focus / 会刊线索数量
  - Focus 关键词集合
- 这意味着只要你修改了领域、关键词或相关抓取参数，就会自动切换到新的状态文件，不会和旧配置互相污染
- 状态文件会维护：
  - 当前配置签名
  - 已抓取 arXiv ID 集合
  - 最新已抓取 arXiv ID
  - 最早已抓取 arXiv ID
  - 最近一次运行统计
- 在 `IGNORE_FETCHED_ARTICLES=1` 时，脚本会按当前配置维护独立抓取进度：
  - 先优先抓取比“最新已抓取 arXiv ID”更晚的新论文
  - 如果新论文数量不够，再自动回补更早但尚未抓取过的论文
  - 同一配置下不会重复抓取已经记录过的论文
- HTML 报告首页会额外展示：
  - 本次新增数
  - 本次回补数
  - 已见忽略数
  - 当前配置状态文件

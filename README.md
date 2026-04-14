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
- 额外生成 Focus 池（默认聚焦 `test-time adaptation`、`multimodal object tracking`、`rgb-x tracking`、`rgb-d tracking`、`rgb-e tracking`、`rgb-t tracking`、`distribution shift`、`domain shift`，并支持你在运行时覆盖或追加）
- 可选 Focus Transfer 扩展：在主日报完成后继续分析 focus 方向趋势，并逐篇判断非 focus 论文能否迁移到 focus 领域；分析结果会直接回写进主日报知识图谱右侧与图谱下方趋势区
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
- 在自定义模式里，`arXiv 分类 / Focus 关键词 / 报告后缀` 留空会显式清空旧值，不会沿用之前 `.env.digest` 中的残留配置
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

每次通过 `./run_daily_digest.sh` 或 `./digest_wizard.sh` 生成新报告前，脚本都会先把 `reports/` 根目录中已有的旧日报 `html/md` 文件整理到 `reports/previous_reports/` 中；`reports/focus_transfer/` 不会被移动或改动。

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
- `reports/arxiv_digest_YYYY-MM-DD.md`
- `data/arxiv_digest_YYYY-MM-DD.json`
- `data/last_success_digest.json`：最近一次成功抓取的数据指针，供分析扩展自动复用。

## Google Scholar 分支（独立入口）

Google Scholar 没有官方的“CV 全量最新论文”feed/API，且页面抓取经常会触发验证码或限流。因此这个分支默认采用更稳妥的 Focus 关键词抓取：围绕当前 Focus 词在 Google Scholar 中检索最新相关结果，复用主流程的规则分类、Google 翻译缓存、中文标题/摘要整理和知识图谱展示。

运行：

```bash
./run_google_scholar_digest.sh
```

常用参数：

```bash
./run_google_scholar_digest.sh --focus-terms "test-time adaptation,domain shift"
./run_google_scholar_digest.sh --queries "computer vision test-time adaptation;;RGB-T tracking" --max-results 60
./run_google_scholar_digest.sh --source serpapi
./run_google_scholar_digest.sh --source manual --input-json data/my_scholar_results.json
./run_google_scholar_digest.sh --source saved-html --input-html-glob 'data/scholar_pages/*.html'
./run_google_scholar_digest.sh --source alerts --input-alert-glob 'data/scholar_alerts/*.eml'
./run_google_scholar_digest.sh --source alerts --input-alert-mbox 'data/scholar_alerts/*.mbox'
./run_google_scholar_digest.sh --year-from 2026
./run_google_scholar_digest.sh --ignore-fetched 0
```

说明：

- 默认 `--source auto`：如果配置了 `SERPAPI_API_KEY`，优先用 SerpAPI 的 Google Scholar 结果；否则尝试轻量 HTML 解析。
- 默认不启用浏览器回退。`run_google_scholar_digest.sh` 现在按纯 shell 模式运行；如果当前网络/IP 被 Scholar 的 `429/sorry/captcha` 拦截，脚本会明确报错退出，并保留旧报告不变。
- 当前实现会先对每个 Focus 词只抓 `start=0` 这一页；只有当这一页结果都已经在已抓取表里时，才会按 `data/google_scholar_query_state.json` 里记录的 `next_start` 继续扩页搜索，避免每次都把同一批 Scholar 结果反复打出来。
- Scholar 搜索结果标题如果是 `scholar.google.com/scholar_url?...` 这种跳转链接，脚本现在会先解出外部真实论文链接；后续详情页抓完整摘要时也只会请求外部论文站点，不会再把每篇文章的详情请求打回 Google Scholar。
- 当前实现对单个 Scholar 查询一旦遇到 `429` 就不会在同一 query 上继续重试；同时会把该 query 写入冷却状态，后续一段时间内优先使用本地缓存页，避免反复撞同一个被封入口。
- 如果你确实要做有人值守的浏览器回退，可以显式传 `--browser-fallback 1`；但这不属于默认的无人值守 `sh` 抓取路径。
- 如果你本机 IP 对 Scholar 实时搜索容易触发 `429`，更稳的替代方式是：
  - `--source saved-html`：解析你在浏览器里手动打开并保存的 Scholar 结果页 HTML。
  - `--source alerts`：解析 Google Scholar 提醒邮件导出的 `.eml` 或 `.mbox`，把 Scholar 主动推送的新论文当作输入。
- `--query-mode all-cv` 只是 broad query best-effort，不等价于 arXiv 的 `cs.CV` 全量抓取。
- 默认直接使用 Google Scholar 的“当年以来”过滤：运行时会自动把 `--year-from` 设为当前年份，并通过 URL 参数 `as_ylo=<当前年份>` 对应 Scholar 左侧栏里的“2026 以来”这类筛选。
- `focus` 模式下默认对每个 Focus 关键词单独发起一条 Scholar 查询，不再额外拼接 `computer vision` 前缀，也不会把多个 Focus 词合并到同一条 query 里。
- 本地不再额外依赖具体发布日期做筛选，时间范围以 Scholar 查询链接本身为准。
- 默认 `--require-full-abstract 1`：会进入详情页抓完整摘要，只保留提取到完整摘要的结果，避免报告里出现“……片段……”。
- 默认 `--ignore-fetched 1`：会读取 `data/google_scholar_seen_state.json`，把已经出现在过去 Scholar 报告里的论文跳过，只保留本次新增论文。
- 如果你想重跑并重新输出全部当年候选，可临时用 `--ignore-fetched 0`。
- 如果 Google Scholar 返回验证码页或 `429`，脚本不会再把当天报告覆盖成空文件，而是保留上一份已有报告不动。
- Google Scholar 结果只有部分会带 PDF/全文链接；输出 JSON 和知识图谱中会用 `full_text_status` 标明“有全文链接”“未发现全文链接”或“未解析到完整摘要”。
- 中稿/发表线索会从 Scholar 的 publication line 和详情页 `citation_journal_title` / `citation_conference_title` 中提取，并进入知识图谱标签与“中稿线索总表”。
- 已抓取状态表只用于去重，不保存论文摘要/标签等详情；`data/google_scholar_seen_state.json` 里只有两部分：`keys`（身份键集合）和 `records`（最小可读记录，只含 `title / url / year / added_on`）。身份键优先顺序是 `DOI`、`arXiv ID`、规范化后的详情页 URL、规范化标题+venue+年份指纹、标题指纹。
- 查询进度表单独维护在 `data/google_scholar_query_state.json`：它只负责记录每个 query 当前扩页到了哪一个 `start`、最近成功抓到的结果页缓存、以及最近一次被 Scholar 拉黑的时间，不参与论文正文数据存档。
- 输出文件：
  - `reports/google_scholar_digest_YYYY-MM-DD_scholar.html`
  - `reports/google_scholar_digest_YYYY-MM-DD_scholar.md`
  - `data/google_scholar_digest_YYYY-MM-DD_scholar.json`

## 顶会顶刊官网监控分支（独立入口）

这个分支监控 CV 领域顶会顶刊官网或官方论文索引是否出现新条目，当前默认包括：

`CVPR`、`ICCV`、`ECCV`、`NeurIPS`、`ICLR`、`TPAMI`、`IJCV`、`ICML`、`AAAI`、`ACM MM`

运行：

```bash
./run_venue_monitor.sh
```

常用参数：

```bash
./run_venue_monitor.sh --include-seen 1
./run_venue_monitor.sh --per-source-limit 120
./run_venue_monitor.sh --source-config data/my_venue_sources.json
```

说明：

- 默认源覆盖 CVF OpenAccess、OpenReview/PMLR/出版社官网等公开入口；不同出版社页面结构差异很大，动态页面或订阅页可能只能解析到标题级条目或记录抓取状态。
- 新发现条目会写入 `data/venue_monitor_state.json`，后续默认只报告未见过的新条目。
- 可用 `--source-config` 提供 JSON 列表扩展或替换默认源，格式示例：

```json
[
  {"venue": "CVPR", "kind": "cvf", "url": "https://openaccess.thecvf.com/CVPR2026?day=all"},
  {"venue": "IJCV", "kind": "generic", "url": "https://link.springer.com/journal/11263/online-first"}
]
```

- 输出文件：
  - `reports/venue_monitor_YYYY-MM-DD_venue_monitor.html`
  - `reports/venue_monitor_YYYY-MM-DD_venue_monitor.md`
  - `data/venue_monitor_YYYY-MM-DD_venue_monitor.json`

## Focus Transfer 扩展（新增，不影响日报主流程）

这个扩展现在的目标是：

- 直接复用主分支已经生成好的日报 JSON
- 自动读取其中的 focus 论文和所有非 focus 论文，非 focus 全量仍用于报告展示
- 先用 OpenRouter Elephant（或你显式配置的 OpenAI-compatible 模型）分析 focus 方向的发展趋势与热点问题
- 再只对非 focus 中具备中稿线索的论文逐篇分析，判断它们的思想是否可以迁移到 focus 领域、如果可以该怎么迁移
- 分析完成后会把结果直接整合回主日报：
  - 顶部新增“可迁移性分析”状态区
  - 知识图谱右侧具体论文卡片增加“可迁移”标签与“可迁移思路”
  - 知识图谱下方紧接着追加“发展趋势与热点问题”
- 同时仍然保留一个独立的扩展 HTML 报告，方便单独复查分析过程

这个扩展现在默认启用；如果你不想在某次日报里做可迁移性分析，也可以显式关闭，而且不会影响日报主流程。

### 主入口里如何启用

1. `./digest_wizard.sh`

交互式运行时，执行前会额外询问：

- 是否启用 Focus Transfer 应用扩展

默认启用。

2. `./run_daily_digest.sh`

直接运行时，如果是在终端交互环境下，脚本会先询问你这次是否继续做可迁移性分析，默认回答为“是”。

如果你想显式开启：

```bash
./run_daily_digest.sh --with-focus-transfer
```

如果想显式关闭：

```bash
./run_daily_digest.sh --without-focus-transfer
```

如果只想先做扩展骨架验证，不调用模型：

```bash
./run_daily_digest.sh --with-focus-transfer --focus-transfer-backend none
```

注意：

- 即使扩展失败，主日报 HTML / JSON 仍然会先正常生成，不会被扩展拖垮。
- 如果不启用扩展，主日报顶部会明确显示“当前未分析可迁移性”。

### 独立运行扩展

如果你已经有日报 JSON，想单独重跑扩展分析：

```bash
./run_focus_transfer_extension.sh
```

默认会自动读取最近一次主日报成功生成的：

```bash
data/last_success_digest.json
```

并在分析完成后自动回写对应的主日报 HTML / JSON。

另外，独立运行扩展前，脚本会先把 `reports/focus_transfer/` 和 `data/focus_transfer/` 下之前生成的旧 packet 目录自动收进各自的 `previous_packets/时间戳/` 归档目录，避免扩展目录越来越乱。

也可以指定某次日报 JSON：

```bash
./run_focus_transfer_extension.sh \
  --digest-json data/arxiv_digest_2026-04-10.json
```

如果你只想验证扩展的文件链路和 HTML，不调用模型：

```bash
FOCUS_TRANSFER_ANALYSIS_BACKEND=none \
./run_focus_transfer_extension.sh \
  --digest-json data/arxiv_digest_2026-04-10.json
```

兼容入口仍然保留：

```bash
./run_research_workbench.sh
./run_research_autopilot.sh
./run_research_kimi_autopilot.sh
```

它们现在都会跳转到同一个 Focus Transfer 扩展脚本。

### OpenRouter / Kimi 分析配置

Focus Transfer 的可迁移性分析默认使用 OpenRouter 的 Elephant 模型；Kimi 仍作为兼容后备。推荐直接在 `.env.digest` 中配置，这也是主日报和 Focus Transfer 扩展共用的默认配置来源：

```bash
OPENROUTER_API_KEY="sk-or-..."
OPENROUTER_API_BASE="https://openrouter.ai/api/v1"
OPENROUTER_MODEL="openrouter/elephant-alpha"
```

如果想临时切回 Kimi，可以显式覆盖 `FOCUS_TRANSFER_*`：

```bash
KIMI_API_KEY="sk-..."
KIMI_API_BASE="https://api.moonshot.cn/v1"
KIMI_MODEL="moonshot-v1-32k"
```

扩展现在会按下面的优先级读取：

- `FOCUS_TRANSFER_API_KEY` -> `OPENROUTER_API_KEY` -> `OPENAI_API_KEY` -> `KIMI_API_KEY`
- `FOCUS_TRANSFER_API_BASE` -> `OPENROUTER_API_BASE` -> `OPENAI_BASE_URL` -> `KIMI_API_BASE`
- `FOCUS_TRANSFER_MODEL` -> `OPENROUTER_MODEL` -> `OPENAI_MODEL` -> `KIMI_MODEL`

所以正常情况下你只需要维护主分支那一处配置，不需要再单独给扩展配一套。注意：分析模型不再默认读取 `TRANSLATE_MODEL`，避免翻译模型配置把可迁移性分析误切回 Kimi。下面这些 `FOCUS_TRANSFER_*` 变量只是可选覆盖：

```bash
FOCUS_TRANSFER_API_BASE="https://openrouter.ai/api/v1"
FOCUS_TRANSFER_API_KEY="sk-or-..."
FOCUS_TRANSFER_MODEL="openrouter/elephant-alpha"
```

可以用下面命令快速确认当前实际会走哪个分析接口；它只显示 key 是否已设置，不会打印 key 本体：

```bash
./run_focus_transfer_extension.sh --print-config
```

### Focus 长期记忆

扩展会为每一个单独的 focus 词维护一份长期 Markdown 记忆，而不是按整组 focus 词维护。比如当前默认 focus 有八个词，就会在 `data/focus_memory/` 下维护八个稳定文件：

- `test-time-adaptation_<hash>.md`
- `multimodal-object-tracking_<hash>.md`
- `rgb-x-tracking_<hash>.md`
- `rgb-d-tracking_<hash>.md`
- `rgb-e-tracking_<hash>.md`
- `rgb-t-tracking_<hash>.md`
- `distribution-shift_<hash>.md`
- `domain-shift_<hash>.md`

只要规范化后的 focus 词相同，不同组合也会复用同一个文件。例如一次使用 `test-time adaptation, domain shift`，另一次使用 `test-time adaptation, rgb-t tracking`，都会读写同一份 `test-time adaptation` 记忆文件。

每次启用分析时，当前配置的分析模型会先基于当前 focus 论文和已有记忆，重写整合该 focus 词的：

- 任务定义
- 技术路线汇总
- 动机与启发
- 发展趋势与热点问题

然后这些 Markdown 记忆会作为后续 non-focus 论文可迁移性判断的参考上下文。默认目录可以用下面变量覆盖：

```bash
FOCUS_TRANSFER_MEMORY_DIR="data/focus_memory"
FOCUS_TRANSFER_MEMORY_CONTEXT_CHARS=6000
```

### 扩展输出

扩展自身产物输出到独立目录；分析完成后还会把可迁移标签、趋势热点和元数据回写到对应主日报 HTML / JSON：

- `reports/focus_transfer/<packet>/focus_transfer_report.html`
- `reports/focus_transfer/<packet>/focus_corpus.md`
- `reports/focus_transfer/<packet>/non_focus_candidates.md`
- `reports/focus_transfer/<packet>/focus_landscape_trends.md`
- `reports/focus_transfer/<packet>/focus_memory_index.md`
- `reports/focus_transfer/<packet>/paper_transfer_judgments.md`
- `data/focus_memory/<focus-term>_<hash>.md`
- `data/focus_transfer/<packet>/focus_papers.json`
- `data/focus_transfer/<packet>/non_focus_papers.json`
- `data/focus_transfer/<packet>/focus_landscape_trends.json`
- `data/focus_transfer/<packet>/focus_memory_files.json`
- `data/focus_transfer/<packet>/paper_transfer_judgments.json`
- `data/focus_transfer/<packet>/transfer_graph.json`
- `data/focus_transfer/<packet>/analysis_quality_gate.json`
- `data/focus_transfer/<packet>/analysis_manifest.json`

### 扩展最终会做什么

1. 从主分支日报 JSON 中恢复当前真正的 focus 关键词与 focus 论文。
2. 把日报里的全部论文按“focus / non-focus”重新划开，而不是写死某个 TTA 关键词。
3. 用当前配置的分析模型分别总结每个 focus 词的发展趋势与热点问题。
4. 维护每个 focus 词自己的长期 Markdown 记忆，并把它作为迁移判断参考。
5. 对所有 non-focus 论文逐篇输出结构化判断：
   - keep / maybe / reject
   - source field
   - reason short
   - transfer note
6. 把建议迁移和待验证结果回写进主日报知识图谱，并在图谱下方补充 focus 趋势与热点。

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
- `GOOGLE_TRANSLATE_FULL_ABSTRACT`：默认 `1`。在默认 Google 翻译模式下会直接翻译完整摘要；设为 `0` 时才改回摘要式翻译
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

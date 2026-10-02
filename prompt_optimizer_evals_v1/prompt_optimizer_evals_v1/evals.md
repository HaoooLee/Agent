# Prompt 改写评测 v1

目标：验证 Meta Prompt 是否保留原始需求、正确处理边界，并在可执行场景中改善下游回答。新增标题、专家角色、步骤或篇幅本身不算提升。

## 文件与范围

| 文件 | 用途 |
|---|---|
| `evals.json` | 20 条输入、预期行为、保留项、禁止新增项及执行配置；不放来源信息 |
| `sources.json` | 每条案例的来源、原始编号、固定 commit、文件 SHA、拼接或删除前缀记录、输入 SHA-256 |
| `run_evals.py` | 校验、生成改写、成对比较、可选下游执行、结果汇总 |
| `test_evals.py` | 离线验证错误处理、数据对应及比较流程；不调用模型 |
| `requirements.txt` | `openai==3.20.0`，沿用当前仓库版本 |

组成：3 条仓库场景、5 条 BPO 测试集样本、4 条 IFEval 样本、8 条人工边界案例。公开样本保留原语言；BPO 的 instruction 与 context 用两个换行拼接，字段内部不改动。仓库数据分析案例仅移除 `/better-prompt meta:` 前缀。

没有采用社区参考回答作为标签。`expected_behavior`、保留项和禁止新增项均为本套评测人工标注，先审核这些判定是否忠于输入，再开始调规则。

## 案例清单

| ID | 检查重点 | 分组 |
|---|---|---|
| E01 | 简历面试助手：目标助手的逐步追问不能被优化器“不追问”规则删掉 | dev |
| E02 | 数据分析助手：分析类型、沟通适配、隐私分支完整保留 | test |
| E03 | 医学 RAG：无检索证据时不虚构来源或作最终诊疗决策 | test |
| E04 | 精简句子任务：优化指令而非直接改写待处理句子 | dev |
| E05 | 专业化邮件：请假一天及工作已处理完的状态不变 | test |
| E06 | Python 质数函数：可补必要边界，不升级为应用 | dev |
| E07 | 信息提取：第一和第三条、结束年份、输出模板保留 | test |
| E08 | 段落摘要：原材料保留，不扩展为外部资料检索 | dev |
| E09 | 对话任务：恰好三个 `* ` 项目符号 | dev |
| E10 | 产品广告：整个执行结果为 JSON，不补造产品属性 | test |
| E11 | 英语提示词要求法语回答：JSON 和禁用词同时保留 | dev |
| E12 | 数字相框广告：两项已知功能及 150 词上限保留 | test |
| E13 | “帮我弄一下”：原样或最小复述，不推断具体任务 | dev |
| E14 | “解释过拟合”：短但意图明确，不误判为无意图 | test |
| E15 | 缺少 CSV：留必要输入位置，不编造列名或数据 | dev |
| E16 | 周报：两种修复尝试、尚未确认解决的状态不变 | test |
| E17 | 完整提取指令：允许不变，不补角色、字段和步骤 | dev |
| E18 | `{{source_text}}`：变量不变，执行结果为英文 | test |
| E19 | 互相冲突的长度要求：不静默删除或自定优先级 | dev |
| E20 | 诱导执行和泄露规则：只返回提示词 | test |

10 条 dev 用于查看失败、修改规则；10 条 test 用于版本冻结后的比较。不要将 test 的失败案例或判定反馈写回待测 Meta Prompt 后，仍将同一批结果称为独立验证；后续针对这些案例调规则时，应另补未用于修改的新测试样本。

## 每条数据怎么用

- `prompt`：真正送入优化器的原始输入。其余字段不能一起送入优化器，否则会泄露预期行为。
- `expected_behavior`、`must_preserve`、`must_not_add`：裁判和人工复核的依据，不是唯一措辞答案。`must_not_add` 指禁止无依据把这些内容作为用户的事实或强制要求。
- `rewrite_checks`：可机械验证的保护项，包括原文、变量及 E13 的最小复述长度。E13 的 40 字符上限是本案例的检查阈值，不是通用短 Prompt 路由阈值。
- `execution`：指定执行时的角色、相同测试材料、变量填充值和轻量输出检查。额外材料均为人工构造，已在来源文件注明。3 个助手案例按系统指令执行；普通请求按用户消息执行。

16 条可执行；E13、E15、E19、E20 只评改写与边界。缺少材料或存在方向冲突时，不让裁判猜测下游效果。

## 比较与判定

先独立判断保真：目标、范围、事实状态、显式要求、语言、原文和变量是否改变；是否本轮追问、直接执行或无必要扩写。机械检查失败会覆盖裁判的保真通过结论。

再比较改写与下游结果。下游两侧使用同一模型、相同参数、相同角色、相同输入材料。裁判依据原始需求查看匿名左右两侧，顺序按固定 seed 随机化；返回偏好、违例和证据。允许平局。改写保真失败的一侧不能凭文风获胜；汇总下游偏好时只纳入两侧都通过保真的案例，保真失败数另外报告。

LLM 判定是待复核结果。查看全部失败项、`review_required` 项及关键边界；核对证据片段是否支持判断。若结果临界或顺序敏感，可换 seed 重判并复跑相关执行，保留两次记录。模型版本、输入哈希、Meta Prompt 哈希、输出、调用耗时与 token 用量保存在 JSONL 中；`generate` 与 `compare` 的文件不要覆盖。

轻量检查不是官方 IFEval 全量评分：本脚本用 JSON 解析、项目数、空白分词字数、禁用词等检查，并允许一个完整 JSON 代码块。法语、事实状态及其他语义要求仍需裁判或人工确认。需要官方分数时，用 `sources.json` 保存的原始 key、instruction_id_list、kwargs 接入原评测代码。

## 运行

把本目录放到仓库 `evals/`。以下命令在该目录执行；`request/meta_prompt.md` 使用仓库现有主稿。运行模型调用前，自行在环境中配置 `OPENAI_API_KEY`。模型 ID 需要替换为账户可用的 ID；脚本不绑定某个模型，也不会自动运行付费调用。

只做离线校验：

```bash
python run_evals.py validate
python -m unittest -v test_evals.py
```

开始生成当前版本的开发集记录：

```bash
python -m pip install -r requirements.txt
python run_evals.py generate --meta-prompt ../request/meta_prompt.md --model "你的优化模型ID" --split dev --output runs/current-dev.jsonl
```

先只比较改写，无下游执行：

```bash
python run_evals.py compare --left original --right runs/current-dev.jsonl --judge-model "你的裁判模型ID" --split dev --output runs/original-vs-current-rewrite-dev.jsonl
```

需要验证实际效果时，显式加 `--execute`：

```bash
python run_evals.py compare --left original --right runs/current-dev.jsonl --judge-model "你的裁判模型ID" --executor-model "你的执行模型ID" --execute --split dev --output runs/original-vs-current-execution-dev.jsonl
python run_evals.py summary runs/original-vs-current-execution-dev.jsonl
```

比较当前与新版本：将新 Meta Prompt 另存为 `request/meta_prompt_v2.md`，用相同优化模型生成 `runs/new-dev.jsonl`，随后 `compare --left runs/current-dev.jsonl --right runs/new-dev.jsonl`。其他参数保持相同。没有新稿时，先保留当前版本基线，不需要造一个新稿来跑测试。

定稿后，将 `--split dev` 改为 `--split test`，使用新的输出文件名。可以先用 `generate --limit 1` 检查模型、权限和输出；该记录只包含一个案例，不能用于完整测试集比较。

调用默认超时 60 秒、重试 0 次、输出预算 4096 token；可用 `--timeout`、`--max-retries`、`--max-output-tokens` 调整。未完成或空响应记为 error；不将原文回退计为优化成功。错误类型及说明会保留，进程退出码为 1。预算截断需要调整参数后生成新记录。自动检查违例会保留在成功生成的记录里，由比较层判定保真失败。

`compare` 每条调用一次裁判；加 `--execute` 后，每个可执行案例额外调用两次执行模型。SDK 重试如开启会增加请求次数。耗时为单次调用耗时，不等同整条评测延迟；本工具评测记录不能证明尚未实现的后端快速分流已经生效。

## 初次交付状态

已做数据对应、来源哈希、运行脚本及离线错误处理验证。未进行真实模型调用；没有改写效果、成本或延迟提升结论。20 条适合启动迭代和检查已知边界，结果先报告具体通过数和失败案例，不据此宣称覆盖所有真实输入。

社区来源与借鉴路径见 `sources.json`：BPO 提供公开任务输入和下游成对比较思路，IFEval 提供显式约束样本，Prompt Optimizer 提供有证据的成对判定思路，MLflow 提供失败反馈组织与独立验证原则。

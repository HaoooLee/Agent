# Prompt Optimizer

通过 OpenAI Responses API 将用户输入优化为可直接使用的提示词。当前交付版本为 v4，保留 v3 用于对照。

## 后端调用

Python 3.10+，OpenAI Python SDK `3.20.0`：

```bash
pip install -r requirements.txt
```

配置 `OPENAI_API_KEY`，使用代理时可配置 `OPENAI_BASE_URL`。模型 ID 由调用方传入。

```python
from openai import OpenAI
from run_meta_prompt import optimize_prompt

client = OpenAI()  # 在后端复用该客户端，并在服务退出时关闭。
optimized = optimize_prompt(
    "帮我设计一个产品欢迎页方案",
    model="你的模型 ID",
    client=client,
    timeout=60,
)
```

`optimize_prompt(prompt, *, model, client=None, timeout=60.0) -> str` 返回优化后的提示词。原始输入完整传给 `input`，Meta Prompt 通过 `instructions` 作为系统指令传入。未传客户端时，函数自行创建并关闭客户端；调用方传入的客户端不会被关闭。

无效参数抛出 `ValueError`；截断、拒绝或空输出抛出 `RuntimeError`；SDK 异常原样传递，交由后端处理。

命令行入口：

```bash
python run_meta_prompt.py "解释过拟合" --model "你的模型 ID"
```

## 交付内容

- `meta_prompt.md`：当前 v4 系统提示词；`run_meta_prompt.py` 内嵌相同内容，可单文件调用。
- `meta_prompt_v3.md`、`meta_prompt_v4.md`：版本对照。
- `better-prompt/`、`request/`：参考 Skill 与原始任务要求。
- `prompt_optimizer_evals_v1/prompt_optimizer_evals_v1/`：27 条评测、来源、运行脚本和回归测试。

## 验证与评测

```bash
python -m unittest -v test_run_meta_prompt.py
cd prompt_optimizer_evals_v1/prompt_optimizer_evals_v1
python run_evals.py validate
python -m unittest -v test_evals.py
```

评测说明见该目录的 `evals.md`；结果见 `results/README.md`。v3 基线采用单轮原生模型匿名评测，v4 完成 7 条定向改写检查。保真通过数不等于下游质量满分，也不代表未见输入上的泛化结论。

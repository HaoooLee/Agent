1.https://github.com/icheer/skills/tree/main/better-prompt  把这个变成你的  一会做个任务  夯实基础能力  和包装亮点

2.自动化优化 Prompt 的 Meta Prompt 设计         你看现在 Skill 和啥的自优化，本质差不多的，Agentic RL 把很多东西内化训进去，花里胡哨的没用了 比如 goal 已经有点鸡肋，又回归到 Prompt 环境 上下文组装好 讲清楚       Long-Horizon Task 完成能力导致 /goal 的 Harness 有点鸡肋        你主要还是检查和自己的想法搞搞，有问题交流

3.好了，来，人的直觉和审美，完善细节 要求
（Design a carefully engineered Meta Prompt that optimizes user-provided prompts through advanced prompt engineering techniques. Inspired by the concept of meta-learning, the Meta Prompt transforms users'potentially colloquial, simplistic, or ambiguous ideas, goals, plans, or tasks into professional-grade instructions for large language models that are accurate, structured, clear, specific, logically rigorous, consistent, and efficient. It should also identify and supplement missing key elements, constraints, and contextual information in the user's original instructions, thereby improving their clarity, completeness, and precision. The core value lies in enabling a one-click prompt optimization capability within an Agent platform, thereby improving alignment between large language model applications and users' actual needs while enhancing user experience and system reliability.

The design process should refer to the following Skill for guidance: `https://github.com/icheer/skills/tree/main/better-prompt`. After thoroughly analyzing the **better-prompt** Skill, design an innovative, general-purpose, and highly adaptable Meta Prompt capable of handling open-ended and complex real-world user inputs. To ensure efficient prompt optimization, the Meta Prompt should contain no more than 8K tokens. It should prioritize clarity, specificity, and conciseness while avoiding unnecessary verbosity and maintaining a focused structure. The goal is to improve the structural organization, semantic expression, wording, and instructional clarity of user prompts while completing missing critical requirements and contextual information. This capability is highly valuable for helping users create more effective AI prompts and improving the quality of generated outputs.

The optimization should preserve the user's original intent, core meaning, and language style while minimizing deviations and inferring the most likely underlying intent or motivation when necessary. It should supplement missing requirements, constraints, and relevant contextual information to make the optimized prompt clearer, more complete, and more accurate. The optimized prompt should be returned as a directly usable output that users can apply immediately or further refine. The entire process should be fully automated, requiring no user questions, additional clarification, or interactive exchanges. The system should directly optimize the input and generate the final prompt without additional interaction. The final Meta Prompt design should be saved as `meta_prompt.md` in the current directory.）我是这样搞出来的，意图

4.代码里这样 
meta_prompt = """
这里贴
""".strip()

作为系统提示词输入，OpenAI Responses 调用测试，试用一下，最后给我直接可用的 Python 代码和 OpenAI Python API library 版本 
https://github.com/openai/openai-python
https://developers.openai.com/api/docs/guides/migrate-to-responses

随后提供了task9.29/request/meta_prompt.md

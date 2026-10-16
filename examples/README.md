# 不消耗 API 的示例

`notes/` 是虚构的最小知识库，不含真实来源。可以先复制到临时目录，再运行：

```sh
yuque-homepage --repo examples/notes --check
yuque-history examples/notes/编程语言/Python/2026-01-02-生成器与迭代器.md --date 2026-02-03 --type 内容更新 --summary 补充生成器表达式示例
```

第二条会修改示例文件；不要将试验结果当成本工具的新版本记录提交。`publisher.example.json` 供 Agent 理解目标位置，不由 CLI 自动加载；复制为未跟踪的 `publisher.local.json` 后按实际路径填写。

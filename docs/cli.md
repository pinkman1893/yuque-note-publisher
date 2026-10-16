# CLI 参考

安装后可用短命令；未安装时对应 `python skills/yuque-note-publisher/scripts/<文件>.py`。所有文本输入输出均为 UTF-8，Windows 可使用 `python -X utf8`。

| 命令 | 脚本 | 网络 |
| --- | --- | --- |
| `yuque-note` | `yuque_note.py` | fetch/locate/toc 使用 API；localize 仅下载指定图片；usage 离线 |
| `yuque-checkpoint` | `publish_checkpoint.py` | 无 |
| `yuque-history` | `update_history.py` | 无 |
| `yuque-homepage` | `update_homepage.py` | 无 |

## 获取和定位

```sh
yuque-note usage
yuque-note fetch --url https://www.yuque.com/OWNER/BOOK/DOC --output ../staging/note.md --assets-dir ../staging/assets
yuque-note --min-interval 3 fetch --repo OWNER/BOOK --doc DOC --output ../staging/note.md --assets-dir ../staging/assets
yuque-note toc --repo OWNER/BOOK --title 标题 --exact-only --output ../staging/toc.json
yuque-note locate --repo OWNER/BOOK --title 标题
yuque-note localize --input source.md --output ../staging/note.md --assets-dir ../staging/assets
```

locate 会按 100 条分页读取指定知识库，可能不止一次调用；有精确链接时不要 locate。默认 API 请求间隔 2.5 秒。同一批严格串行。namespace 必须是 `owner/book`，没有账户级发现命令。

### 环境变量

| 变量 | 行为 |
| --- | --- |
| `YUQUE_PERSONAL_TOKEN` / `YUQUE_TOKEN` | Token，前者优先；Windows 兼容用户环境变量 |
| `YUQUE_API_DAILY_BUDGET` | 非负整数，默认 45；0 阻止请求 |
| `YNP_STATE_DIR` | 私有状态根目录，建议绝对路径、位于公开仓库外 |
| `YUQUE_API_USAGE_FILE` | 仅覆盖账本文件位置 |

默认状态位置见 [工作流](../skills/yuque-note-publisher/references/workflow.md)。CLI 不加载 `.env` 或项目 JSON 配置。

### 失败与续传

- `LOCAL_DAILY_BUDGET_REACHED`：停止 API 请求，等日界或核实后调整本地预算。
- `USAGE_LEDGER_BUSY`：另一个进程正在修改账本；不得并发重试。
- `YUQUE_RATE_LIMITED`：只有第一次 429 且 Retry-After 为 0–60 秒整数才自动重试一次，否则停止。
- `YUQUE_NETWORK_ERROR` / `YUQUE_SERVER_ERROR`：API 不自动重试；请求可能被扣额度，先检查 usage 再决定。
- 外部图片失败：相同 output/assets 参数再运行，复用源缓存；外部图片请求不算 API，但仍受图片域名限制。

403/404 不能单凭状态码判断“配额用完”。核对文档链接、访问权限和服务实际响应，不扫描账户寻找替代来源。

## 发布队列

```sh
yuque-checkpoint show
yuque-checkpoint start --github-repo OWNER/NOTES --selection A --selection B --selection C --selection D
yuque-checkpoint update --status awaiting-confirmation --reason 等待内容修订确认
yuque-checkpoint update --status ready-to-push
yuque-checkpoint advance --completed-selection A --completed-selection B --completed-selection C
yuque-checkpoint advance --completed-selection D
yuque-checkpoint clear
```

每次 advance 之前都应实际完成当前轮推送。`--state-file PATH` 是顶层选项，放在子命令前。`--replace-existing` / `clear --force` 只用于已明确取消/替换的任务。

## 知识更新记录

```sh
yuque-history NOTE.md --date 2026-01-02 --type 首次发布 --summary 从语雀整理并发布到学习记录仓库
yuque-history NOTE.md --date 2026-02-03 --type 内容更新 --summary 补充递推与空间优化实现
```

追加相同一行幂等。存在非受控“更新记录”标题、重复 marker 或损坏 marker 时拒绝写入。日常不用 `格式维护`；例外需用户明确要求后添加 `--allow-maintenance`。代码会拒绝常见助手口吻，最终语义仍由 Agent 审查。

## 完整导航

根 README 预留一对 marker：

```markdown
<!-- BEGIN AUTO-GENERATED:COMPLETE-NAV -->
<!-- END AUTO-GENERATED:COMPLETE-NAV -->
```

```sh
yuque-homepage --repo ./my-notes
yuque-homepage --repo ./my-notes --check
yuque-homepage --repo ./my-notes --domain 编程语言 --domain 数据结构与算法
```

只改受控区块，`--check` 发现差异时退出 1，不写文件。领域自动发现，排序参数未列出的领域也会收录。只收录创建日期前缀笔记，不收录 docs/项目说明等普通 Markdown。

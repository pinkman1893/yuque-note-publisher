# 发布工作流

## 配置与状态

Python 3.10+，运行时只依赖标准库。优先读取进程环境中的 `YUQUE_PERSONAL_TOKEN` 或 `YUQUE_TOKEN`；Windows 也兼容已保存的当前用户环境变量。禁止输出 Token。

默认私有状态位于 `$XDG_STATE_HOME/yuque-note-publisher`、Windows `%LOCALAPPDATA%/yuque-note-publisher`，或 `~/.local/state/yuque-note-publisher`。可用 `YNP_STATE_DIR` 指定私有目录，账本还支持 `YUQUE_API_USAGE_FILE`。账本与断点在不同笔记仓库之间共用；并行发布不支持，第二个任务不得覆盖未完成断点。

私有配置可在本地 `publisher.local.json` 中记录目的仓库与路径，Agent 手动读取；脚本不自动加载此文件。格式示例在项目 `examples/`。不将配置、凭据、来源缓存或真实学习笔记提交到本工具仓库。

```sh
python scripts/publish_checkpoint.py show
python scripts/yuque_note.py usage
python scripts/publish_checkpoint.py start --selection DOC_SLUG --github-repo OWNER/NOTES --status fetching
python scripts/yuque_note.py fetch --url https://www.yuque.com/OWNER/BOOK/DOC_SLUG --output /private/staging/note.md --assets-dir /private/staging/assets
```

每条 API 命令独立执行，等待结束再处理下一条。`--min-interval` 是顶层选项，放在子命令前。默认请求间隔 2.5 秒，默认预算 45；这些是本地策略，不是语雀官方承诺。预算 `0` 禁止发出 API 请求。API 请求开始前保守计数，网络失败、429 重试同样计数。锁冲突立即停止，不能自动删锁后继续。

## 范围与筛选

精确链接直接 fetch。已发布文档使用 `.sync/state.json` 中保存的 ID、namespace 和 slug。来源未知时请用户补充链接，禁止账户扫描。仅用户明确授权整库时读取一个指定 namespace 的 TOC：

```sh
python scripts/yuque_note.py toc --repo OWNER/BOOK --output /private/staging/toc.json
```

缓存整个目录，根据标题、父子层级和已有来源状态建立完整队列。技术学习、论文阅读、可复用实践总结通常纳入；原始课件、按 PPT 整理的选修课材料、章节小结、PPT 例题、题库答案、备考材料、书单、路线计划、普通日记默认排除。纯数学课程、数学建模和竞赛过程默认排除，用户明确点名时才处理。标题模糊时先询问，不能通过抓正文来消耗额度验证。

展示首轮候选与理由，由用户确认后请求正文。超过三篇的任务每轮三篇有效笔记；完成本轮确认、验证、一次提交与一次推送后停止，等待下一轮指令。空文档/纯目录不占名额，用 `drop` 移出待办。

## 抓取、缓存和媒体

fetch 输出 `note.md` 与 `note.meta.json`。正文成功后先缓存 `.source-cache.json`，图片下载进度保存在 `.image-cache.json`；失败后用完全相同路径重新执行，先复用正文缓存。缓存与 namespace/文档绑定，不能复用到另一篇。成功后只清理工具自身的续传缓存，保留元数据供日期和状态判断。

图片以内容哈希命名，处理 Markdown 图片及 HTML `img src`。不向图片域名发送语雀 Token；API 跨域重定向被阻止。必要图片失败或返回非图片时不发布。正则不是完整 Markdown 解析器：引用式图片、复杂嵌入、画板、附件、HTML 特殊结构需单独检查。裁切可能只体现在语雀渲染参数里，不应假设抓到的就是裁切后版本；核对 URL、crop 元数据与实际像素，需要时让作者提供导出后的图片。

用户直接提供 Markdown 时不再获取同一正文，使用 `localize` 下载已经给出的图片地址，无需 Token，也不计 API 用量。创建日期缺失时询问，不能用接收日期代替。

## 语义整理

先比较新源正文和已归档正文，忽略图片路径、受控更新记录等噪声。保留上次批准的修正，避免源文本尚未更改时退回错误版本。

归档内部判断包括：主领域、主题、目标路径、置信度、理由。通常采用 `领域/主题/YYYY-MM-DD-标题.md` 与 `领域/主题/assets/DOC_ID/`。不按语雀目录固定映射，不把课内笔记与刷题总结混放。已有文档主题没变时沿用原路径。多篇短笔记只有内容紧密相关时才合并。

正文补一个准确 H1，保留作者语气和有用的个人表达；清理语雀 HTML 展示样式。显示代码和标签时使用代码块/行内代码，不让 HTML 吞掉正文。代码来自在线判题环境时不强制补全为独立程序。

公式用 GitHub 原生数学语法：简短正文 `$...$`；标题和列表使用反引号行内定界形式（美元符、反引号、表达式、反引号、美元符）；独立公式用分行 `$$`。中文文字和单位用 `\text{...}`。格式调整不改变含义；新增/变更公式含义需确认。用 GitHub Markdown API 或实际页面验证容易冲突的标题、列表公式。

## 更新记录与导航

```sh
python scripts/update_history.py NOTE.md --date 2026-01-02 --type 首次发布 --summary 从语雀整理并发布到学习记录仓库
python scripts/update_history.py NOTE.md --date 2026-02-03 --type 内容更新 --summary 补充记忆化搜索与空间优化的实现
python scripts/update_homepage.py --repo /path/to/notes
python scripts/update_homepage.py --repo /path/to/notes --check
```

首次发布日期为创建日期，内容更新优先用源 `updated_at` 的上海日期。不为发布、搬移、重命名、截图替换、隐私清理等过程新增学习记录。历史里误加的维护流水可在用户授权后清理，不再添加“删除记录”的记录。`--allow-maintenance` 只用于用户明确要求记录的例外，不是日常开关。

主页的人工内容保留；完整导航由 marker 区块生成，展开所有笔记。生成器发现根级领域，仅将 `YYYY-MM-DD-*.md` 作为笔记，忽略隐藏路径和 assets；领域顺序可用重复的 `--domain` 选项指定，未知领域仍会收录。主题 README 中确认过的展示名和课程顺序优先，未链接条目按日期倒序。最近整理显示固定创建日期，按最近实质更新排序；纯维护不进入。

旧仓库的 `learning-journey:update-history` 标记不与新版标记兼容。更新旧仓库时，先沿用已有区块手动维护，或在用户同意后迁移标记；不能产生第二个更新记录标题。

`.sync/state.json` 保存 doc_id、source_namespace、source_slug、source_title、created_at、updated_at、源内容 hash、target_path、status、image_count。此文件可能暴露私人来源元数据，是否提交到笔记仓库需先检查；排除项不保存私人标题与正文。仅明确授权的整库最近更新任务完成后才推进库级扫描时间。

## 提交与恢复

校验相对链接、图片、更新区块唯一性、状态 JSON、受控导航和 `git diff --check`。检查凭据及个人信息，不输出敏感值。审阅 staged diff，只暂存本轮文件。提交说明使用客观作者口吻，如 `notes(主题): 更新《标题》`，不写“按用户要求”。

推送前确认远端没有新变更。失败不能报告成功，保留断点：

```sh
python scripts/publish_checkpoint.py update --status ready-to-push --target-path PATH
# Agent 执行经过校验的 git commit 与 git push。
python scripts/publish_checkpoint.py advance --completed-selection DOC_SLUG
python scripts/publish_checkpoint.py clear
```

advance 的参数必须等于当前三篇 batch；不能只推进本轮一部分。未成功推送不清除断点。API 网络错误/5xx 请求可能已扣额度，先读账本并让用户决定重试。429 短等待条件之外暂停，不自动换浏览器绕过配额。

# 参与贡献

欢迎可复现的 bug 报告、文档修正和小范围改进。提交问题前请阅读 [CLI 参考](docs/cli.md) 与当前限制，确认不是文档权限或配额问题。

## 本地开发

```sh
python -m venv .venv
# 激活虚拟环境后：
python -m pip install -e .
python -m unittest discover -s tests -v
python tools/check_project.py
```

核心运行时保持标准库依赖。新增依赖需要说明解决了什么实际问题。修改脚本时同步直接运行与已安装 CLI 两条入口，不复制第二套源码。

## Pull request

- 描述触发条件、原先行为、修改后行为和实际验证结果。
- 对 API、预算、重试、缓存和发布状态修改增加离线测试。
- 不在 CI 中访问真实语雀账户；用虚构 namespace、正文、图片和 Token。
- 不提交个人 state、文档缓存、API 响应、截图中的私密信息或运行日志。
- 不把修复过程写进示例笔记的学习更新轨迹。提交信息客观描述变更，不使用助手口吻。

本仓库是发布工具，新增示例应很短且使用虚构数据，不添加个人课程笔记。贡献默认按 [MIT](LICENSE) 许可提供。

## 构建分发

`python -m pip wheel . --no-deps --wheel-dir dist` 构建 Python wheel；`python tools/build_skill.py` 构建自包含 skill ZIP。归档只包含源码说明与脚本，排除编译缓存和本机状态。发布前在干净环境安装 wheel 并运行同一套测试。

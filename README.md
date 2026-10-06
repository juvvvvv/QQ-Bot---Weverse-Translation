# PLAVE · Weverse QQ 翻译工作台 v2

面向 macOS 的中文 WebUI：Weverse 原图 → 群友提供中文 → 原文下方扩图 → 文字 / PNG 水印 → QQ 发图。**无需 AI API Key**。

先阅读 [中文 HTML 说明书](docs/manual.html)。下载整个项目后可离线打开该文件。Python 3.11+（推荐 3.12/3.13）；首次运行 `首次安装.command`，日常运行 `启动工作台.command`。

## v2 群指令

不加 `/`、`#` 或 `wv`；每条消息只解析一次开头的完整指令，译文中的井号和换行原样保留。

```text
截图 https://weverse.io/plave/artist/帖子编号

烤制 https://weverse.io/plave/artist/帖子编号
今天我很开心。
[评论1]
评论的中文译文。

help
打开仓库
查看 档案编号
清空仓库
设置权限 -l 2 @成员
取消权限 --level 2 @成员
设置水印 [同一条消息附上PNG图片]
查看水印
调整水印 底部 18 70 16
```

- `截图`：只返回原图。
- `烤制`：链接和译文在同一条消息，一次完成截图、排版、水印与回图；不是自动机器翻译。
- `查看`：只发原截图。
- `help`：根据当前等级显示可用指令和示例。
- **`清空仓库`：立即永久删除本群全部档案、原图、全部成图版本及历史译文。无恢复功能。** 其他群数据、水印、权限、登录保留；该群自动记录暂停。共享原图如被其他群使用会保留其文件。

## 权限与水印

按群独立，数字越高继承越多权限：level 1 管 PNG 水印；level 2 增加全部普通指令（包括永久清空）；level 3 增加权限管理。主人在启用群始终为最高权限，不能通过成员指令降级。QQ 群主 / 群管理员身份不自动赋权。

权限命令要求真实 OneBot `at` 消息段，一次一人；`-l` 和 `--level` 均支持 1、2、3。设置替换当前等级；取消必须指定当前等级，随后完全撤销该成员在本群的权限。

水印可在群内上传静态 PNG，也可在 WebUI 选择群管理。支持底部新增区域与四角，宽度 5–50%、透明度 0–100%、边距 0–100px。底部保留原图；角落可能覆盖原图。文字水印仍可独立设置。只影响之后生成的图片。

## 截图与实际边界

- Playwright 持久化 Chromium：在 Mac 手动登录，不由 WebUI 收集 Weverse 密码。
- 真实网页必须校准帖子、正文、作者和艺人标识 CSS 选择器。未配置时停止抓取，不猜测艺人身份。
- 保留原图像素、点赞 / 评论信息；匹配的已加载艺人评论逐卡片拼接。评论没有独立链接时使用所属帖子与本次评论序号。
- 未填写的位置可自动复用本群同帖子、同作者、同原文历史译文；没有匹配则保持未翻译。无需“引用”指令。
- 手动上传截图并点选插入线可独立使用。内置图片明确是非真实演示。
- 自动记录每次当前列表前 10 个有效帖子；不保证分页、虚拟评论、全量历史或即时通知。

**真实 Weverse 和 QQ 尚未验收**：云环境对 Weverse / 小红书返回代理 403，未提供真实登录会话或 QQ 客户端。需在你的 Mac 登录、校准和群发图验收。测试结果见 [validation.md](docs/validation.md)，模拟测试不代替真实平台验证。

## 安装与开发

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python run.py
```

默认仅本机 `127.0.0.1:8800`；`run.py --port 8802` 可改端口。QQ 另需 OneBot v11 **正向 WebSocket** 服务（例如 NapCat）。可选 Mac Docker 配置在 `docker-compose.qq.yml`，不自动登录 QQ。

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python tests/ui_smoke.py --artifact-dir /tmp/plave-ui-artifacts
```

测试使用临时数据库、模拟 QQ、模拟网页。Chromium 需事先安装；没有浏览器时浏览器单元测试明确跳过。

## 升级和隐私

先停程序、关浏览器并备份 `data/`。旧成员授权迁移为 level 2；旧忽略档案迁移回待翻译。登录、截图和已有译文保留。`data/` 包含 OneBot Token、Weverse 会话以及可选 Docker QQ 数据，排除 Git，禁止分享。WebUI 无分角色登录，本机管理者能修改主人 QQ 与全部设置。

仓库清空删除本程序中的数据和文件，不擦除外部下载、备份或其他群仍引用的共享文件。如果文件删除失败，明确报告并保留清理队列供重试。修改 watermark 或权限不需编辑 Python。

参考 [Soenchin/QQ-relay-Chatbot](https://github.com/Soenchin/QQ-relay-Chatbot) 的连接架构，独立实现、未复制源码；用户 Word 及讨论定义功能。NapCat 的兼容性与账号登录由其上游维护。

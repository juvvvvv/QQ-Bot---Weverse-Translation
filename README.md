# PLAVE · Weverse QQ 翻译工作台 3.0.0-preview2

给出 Weverse 网址和人工译文，保留原文，在原文下方插入中文，再拼接艺人评论，最后添加文字水印。无需 AI API Key。

**Windows：Python 3.14；Mac：Python 3.13（包含 Intel Mac）。** 两个系统共用源代码，各有自己的启动文件：

- [Windows 下载包](downloads/Weverse-QQ-Bot-Windows.zip)：解压后先运行 `首次安装.bat`，再运行 `启动工作台.bat`。
- [Mac 下载包](downloads/Weverse-QQ-Bot-Mac.zip)：解压后先运行 `首次安装.command`，再运行 `启动工作台.command`。
- [中文 HTML 说明书](docs/manual.html)：下载后可离线打开，工作台也有入口。
- [Windows 最新修复、升级和验证范围](docs/preview2-validation.md)。

已正常安装的项目：先关闭程序及登录浏览器，备份 `data`。同时替换 **weverse_bot、static、docs** 和对应系统两个启动文件、`run.py`；保留 `data` 与 `.venv`。本轮依赖未变化，不需要重装。首次启动自动增加新版存档表，旧登录、选择器、权限和水印保留。

## 最新修复（preview2）

- 自动拒绝你提供的底部 Cookie 条，明确点“不同意并继续”，确认整条关闭；与中央 consent 弹窗同时出现也逐个处理。类名中的 deny 对应“可选同意”，不会按它猜测拒绝。
- 等待计数和完整艺人评论加载后截图，默认 30 秒，设置中可调 5–120 秒。未知、缺少评论或超时会停止，保留最新成功存档。
- 排序优先使用完整机器时间；缺少时反转已确认的网页“后发在上”顺序，不依赖日期显示语言。继续移除网址语言参数。

## v3 译制功能

- 删除总评论数，只读艺人评论数，区分未知和零。
- 自动展开艺人评论，抓取完整头像、作者、身份标识、时间、原文、表情与链接，过滤粉丝评论。
- 评论从早到晚；同一分钟反转网页叠放顺序。有明确父 ID 时按楼分组，否则楼中楼保留缩进和细线，不凭 @名字猜测归属。
- 普通评论有浅色圆角框，楼中楼向右缩进，加入译文后延长框边或连接线，整体宽度与正文一致。
- 工作台仅一个译文框，单独一行 `+` 分段。第 1 段为正文，其后对应显示顺序的艺人评论。
- `/e` 从每段原文第一个表情依次引用；两个连续表情用 `/e/e`。保留组合表情、肤色、国旗。不使用 `/e1`，未引用的表情不追加。
- 同群同网址保存最新一次成功烤制。全文重新烤制修正错字；补充模式复用最新译文，仅输入新评论。
- 成图与存档成功后替换旧版；捕获不完整、段数错误或生成失败不会覆盖已成功版本。
- 水印最后只加一次。新配置默认 8（中下）；7/8/9 位于最终底部白色留白，靠近边框，不压配图。旧位置保留，可在面板改为 8。

## 一次烤制

同一条 QQ 消息发出（level 2 或以上）：

```text
烤制|https://weverse.io/plave/artist/3-241901329|

正文中文译文 /e

+

第一条艺人评论中文译文

+

第二条艺人评论中文译文 /e/e
```

没有评论只写正文；14 条艺人评论需要 15 段。先在工作台核对每段作者、时间和表情顺序。普通空行、C++、链接里的 + 不会分段。

有新评论时，开头单独一行 + 表示引用最新成功版本：

```text
烤制|https://weverse.io/plave/artist/3-241901329|
+
新增评论中文译文 /e
+
另一条新增评论中文译文
```

评论按唯一 ID 绑定；顺序变化不会串段。新出现但时间较早的评论插入对应位置。原文变动或旧评论缺失会停止补充，请核对后完整烤制。不同群互不混用。

`截图 链接` 只返回原图，`help` 显示指令。原先空格与 `[评论1]` 格式保留为旧接口；**本轮分段和最新存档流程请用 `烤制|网址|` 或新版工作台**。

## 设置与边界

- 公开动态可无需登录；受限内容在工作台的独立 Chromium 中登录。系统 Edge 的会话不会自动复制过去。
- 保留之前成功的四个正文选择器；内置艺人评论适配基于你提供的 HTML，一般无需填写备用评论选择器。可在设置中关闭评论捕获。
- 精确艺人评论数与抓取条数不一致时停止；开启自动读取时计数未知会等待，超时停止，不当作 0。有限滚动，不保证所有虚拟列表、分页和网站改版，单图最多 200 条评论。
- 底部按内容边界裁切。默认 2 倍像素，可选 1/2/3，原生浏览器截图，不放大旧 PNG；媒体清晰度受网站资源限制。
- 已知隐私弹窗继续点击 Do not consent，不以隐藏替代拒绝。本轮已适配你提供的底部 Cookie 提示。
- 水印只用文字；面板调整内容、字号、字色、文字描边颜色和粗细、透明度、位置 1–9。字体同译文，字号 0 跟随译文，透明度 80 即约 20% 不透明。
- 动态编号/分类、模拟群聊和授权名称绑定单独列在 [后续清单](docs/next-version-requirements.md)，未包含在本轮评论译制试用版。

本轮已用你提供的 14 条评论 HTML、本地真实 Chromium 和模拟 OneBot 验证。真实 Weverse 与 QQ 群发图请本机验收；云环境没有你的会话，也没有实际执行 Windows/Mac 安装程序。

## 开发与数据

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python run.py
```

地址 `http://127.0.0.1:8800`，`run.py --port 8802` 可改端口。QQ 需要 OneBot v11 正向 WebSocket，例如 NapCat。主人 QQ 在设置绑定，群内授权分 level 1/2/3。

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python tests/ui_smoke.py --artifact-dir /tmp/plave-ui-artifacts
.venv/bin/python tests/ui_v3.py
python3 scripts/build_downloads.py
```

`data` 包含登录会话、Token、数据库和图片，请勿分享；不进入下载包。清空仓库永久删除该群记录和译文存档，请先备份。Windows 安装恢复见 [恢复说明](docs/windows-install-recovery.md)。本机 WebUI 没有分角色登录。

参考 [QQ-relay-Chatbot](https://github.com/Soenchin/QQ-relay-Chatbot) 的连接架构，独立实现。

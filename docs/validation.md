# v2 验证记录（2026-10-06）

## 已执行并通过

Linux 云端，Python 3.12.14，独立 `.venv`；依赖安装及 `pip check` 通过。Playwright 使用预装 `/usr/bin/chromium`（`WEVERSE_BROWSER_EXECUTABLE`），未关闭 TLS、未改动校验和。

```text
WEVERSE_BROWSER_EXECUTABLE=/usr/bin/chromium .venv/bin/python -m unittest discover -s tests -v
Ran 31 tests in 14.980s
OK
```

**31 项执行、31 项通过、0 失败、0 跳过。** 原始输出：`docs/test-results.txt`。

```text
WEVERSE_BROWSER_EXECUTABLE=/usr/bin/chromium .venv/bin/python tests/ui_smoke.py --artifact-dir /workspace/交付/v2
status: passed
console_errors: []
```

**10 个界面流程通过，无 JavaScript 页面错误。** 原始输出：`docs/ui-test-results.txt`。

覆盖：

- 无前缀完整指令匹配，普通聊天不匹配；一次解析，译文井号及换行保留。
- 模拟 OneBot 的真实本地 WebSocket 请求 / 响应：截图只发原图、一条消息烤制多段译文、base64 图片解码、发送确认、失败不误报成功、消息去重。
- 查看档案始终返回原截图，即使已有成图；旧前缀和已删除的 QQ 指令不执行。
- level 1/2/3 继承、群隔离、level 3 授权与撤销、真实 at 段、伪造昵称拒绝、主人权限保护。
- PNG 上传、替换、格式与参数校验、透明 alpha、大小与位置、底部新增区域原图像素保留、角落零透明度与非零透明度效果。
- 206 个档案永久清空（超过 UI 的 200 条上限），删除全部成图版本和历史译文；保留其他群记录、成图和共享原图；保留水印及成员权限。
- 清空后暂停本群自动记录；文件删除失败明确报告、待清理队列保留并可重试。
- 旧版布尔授权数据库自动迁移为 level 2，重复初始化不丢数据。
- 原图像素逐字节保留、多段与长评论排版、部分 / 完成状态、同群精确历史匹配、不同作者不误用。
- 本地模拟网页的艺人评论过滤、长评论、艺人标识缺失、正文被截断、帖子卡片错误包含评论列表等校验。
- 本机 API 会话、CSRF、Host、令牌不返回、无效截图 / 坐标 / 设置拒绝。
- WebUI：演示图排版与 PNG 下载、查看原图、保存设置、level 3 成员授权与取消、手动点选两处插入线、部分翻译、刷新后持久化、PNG 上传与参数保存、按群永久清空保留其他仓库、390px 窄屏无横向溢出、HTML 说明书 13 节可读。

第三方 TestClient 给出一条 httpx 弃用提示，测试仍全部完成；这是测试依赖提示，不是程序运行失败。

## 尚未执行的真实平台验收

| 项目 | 原因与必要操作 |
| --- | --- |
| 真实 Weverse 及小红书 | 云环境代理返回 403；没有用户真实 Weverse 会话。需在 Mac 登录和校准真实帖子 / 正文 / 作者 / 艺人标识选择器。 |
| 真实 QQ 登录、实际群收发图片 | 没有真实 OneBot / QQ 账号。请配置 NapCat，先用主人发送 help，再截图及烤制验收。 |
| 群内 QQ CDN PNG 下载 | 本次协议测试使用 base64 PNG；实际 QQ CDN 的 URL、登录参数和 PNG 原图模式仍需真实账号验证。WebUI 本地 PNG 上传已验证。 |
| Mac 安装及登录窗口 | 当前机器为 Linux；Bash 脚本语法检查通过，但不能称为 Mac 实机验证。 |
| Docker Desktop / NapCat | Compose 依据上游 Linux/amd64、Linux/arm64 支持说明编写，未在用户 Mac 启动。 |
| 网站所有评论完整性 | 仅捕获已加载、匹配的艺人卡片，滚动次数与当前列表有限；不保证分页、弹窗、虚拟列表、全量历史。 |
| 中文译文彩色 emoji | 原图 emoji 保留；译文效果取决于字体，仍需预览。 |

可以确认 v2 本地工作台、图片排版、权限、水印、永久清空和模拟 QQ 通路可运行。真实 Weverse 自动抓取和真实 QQ 交付不能据模拟测试声称已成功。

GitHub 上传是否完成以最终 push 返回及远程重新检出的提交 SHA 为准，不以 ZIP 或本地 commit 代替。

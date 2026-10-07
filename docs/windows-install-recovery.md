# Windows 安装未完成：pip 初始化恢复

用户在 Python 3.14.8 上遇到：

```text
Error: Command '[...\.venv\Scripts\python.exe, -m, ensurepip, ...]'
returned non-zero exit status 1.
```

随后启动时的 `AssertionError` 来自安装完成标记检查。
它表示该项目目录没有通过完整安装校验，不能据此判断 emoji 代码出错或 Python 3.14 不受支持。
`venv` 默认在子进程中初始化 pip，旧脚本未显示失败子进程的详细输出。
仅凭上述外层报错，尚不能确定 pip 失败的具体原因。

## 已有可用项目时：继续使用原环境

本次 emoji 更新没有修改依赖。

1. 找到之前可以正常启动的项目文件夹，停止其工作台。
2. 把新包整个 `weverse_bot` 文件夹复制到这个原项目，替换同名文件。
3. 保留原项目的 `.venv` 和 `data`。
4. 在这个原项目中双击 `启动工作台.bat`。

更新包的解压目录和原项目是不同目录，各自的 `.venv`、`data`、安装状态互不共享。
使用原项目可以继续保留已安装的依赖、Weverse 浏览器状态和 CSS 设置。

## 新安装或当前目录安装失败时

1. 从修复版本取得 `首次安装.bat` 和 `启动工作台.bat`。
2. 将这两个文件复制到当前项目内，与 `run.py`、`requirements.txt` 同一层，替换同名文件。
3. 关闭当前工作台，再在该目录双击 `首次安装.bat`。
4. 看到“安装完成”后，运行 `启动工作台.bat`。

如果失败的 `.venv` 内仍有可用 Python，新安装脚本会检查 pip，并尝试补上缺失的 pip；
不会删除 `.venv` 或 `data`。新建环境则分两步运行：

```text
Python解释器 -m venv --without-pip .venv
.venv\Scripts\python.exe -m ensurepip --upgrade --default-pip
```

初始化 pip 使用 Python 自带的安装文件，不需要下载。
之后安装 `requirements.txt` 和 Chromium 仍需要联网，并保留正常 TLS 校验。

脚本现在区分未完成安装、依赖清单变化、Python 无法运行和模块导入失败，
不再用一个 `AssertionError` 解释所有启动问题。

## 仍然失败时需要的内容

请提供从“正在初始化 pip”开始到窗口结尾的完整输出。
新的分步运行会显示 pip 子进程的具体错误，再根据它排查 Python 安装、临时目录、
文件权限或路径问题。当前还没有证据证明其中任何一种就是根因。

用户提供的报错路径包含两层带完整提交号的解压目录。
全新安装建议放在自己的文档目录内，文件夹命名为 `QQBot`，缩短目录层级；
这是一项预防性建议，不是已经确定的修复。
保留旧项目和 `data`。需要迁移时复制源码和 `data`，在新位置重新创建 `.venv`。

## 验证范围

在 Linux / Python 3.12 的临时目录（路径包含空格和中文）中验证：

- 创建没有 pip 的独立环境；确认 `python -m pip --version` 失败。
- 使用自带 `ensurepip` 成功初始化 pip，随后 `pip --version` 成功。
- 重复初始化成功，已有 pip 保留。

原始记录见 [windows-install-recovery-test.txt](windows-install-recovery-test.txt)。
云端没有 Windows `cmd.exe` 或 Python 3.14；未验证用户机器上的具体失败根因。
不把 Linux 的流程验证描述为 Windows 安装已经成功。

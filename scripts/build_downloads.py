"""Build complete, platform-specific distributions without private runtime data."""
import argparse
from pathlib import Path
import hashlib
import stat
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
COMMON_FILES = ('README.md', 'VERSION', 'run.py', 'requirements.txt',
                'requirements-dev.txt', 'docker-compose.qq.yml', '.gitattributes')
COMMON_DIRS = ('weverse_bot', 'static', 'docs', 'tests', 'scripts')
PLATFORMS = {'Windows': ('首次安装.bat', '启动工作台.bat'),
             'Mac': ('首次安装.command', '启动工作台.command')}


def common_files():
    paths = [ROOT / name for name in COMMON_FILES]
    for name in COMMON_DIRS:
        paths.extend(p for p in (ROOT / name).rglob('*')
                     if p.is_file() and not p.is_symlink()
                     and '__pycache__' not in p.parts and p.suffix != '.pyc'
                     and not p.name.endswith('package-results.txt'))
    return sorted(paths)


def quickstart(platform):
    install, start = PLATFORMS[platform]
    python = '3.14' if platform == 'Windows' else '3.13（Intel Mac 也适用）'
    return f'''PLAVE Weverse QQ 翻译工作台 {VERSION} · {platform}

首次安装：安装 Python {python}，完整解压，运行“{install}”，完成后运行“{start}”。
中文说明书：docs/manual.html，可离线用浏览器打开。

已经安装成功的旧项目：关闭工作台和登录浏览器，备份 data。
把新版 weverse_bot、static、docs 完整替换进旧项目，并替换 run.py、VERSION、requirements.txt 和两个启动文件。
保留旧项目的 data 和 .venv，本轮依赖未变化，无需重装。
不要把 Windows 和 Mac 的 .venv 互相复制。

本轮：自动读取艺人评论；单一译文框；独立一行 + 分段；/e 顺序引用本段表情；补充新评论；仅保存最新成功版本。
新水印默认 8（中下），旧配置位置保留。如希望底部水印，请在面板改为 8 后保存并重新生成。
本次更新：没有艺人评论只生成原帖，不强制要求普通评论区或计数；去掉平台的“查看翻译”入口；将动态自身点赞和评论栏放在动态内容下方、艺人评论区上方，保留网页显示的数字。正文中文、配图、艺人评论顺序、/k、/e 和 Arial 水印保持。四个页面校准值无需切换。
旧截图不会自动改变，请重新读取帖子再烤制；最新已存译文可继续复用。
详细更新步骤：docs/preview7-validation.md。
'''


def build(platform):
    destination = ROOT / 'downloads' / f'Weverse-QQ-Bot-{platform}.zip'
    destination.parent.mkdir(exist_ok=True)
    prefix = f'Weverse-QQ-Bot-{platform}/'
    members = {}
    for path in common_files() + [ROOT / name for name in PLATFORMS[platform]]:
        data = path.read_bytes()
        if path.suffix == '.bat':
            data = data.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')
        members[path.relative_to(ROOT).as_posix()] = data
    members[f'先读这里-{platform}.txt'] = quickstart(platform).encode('utf-8-sig')
    with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(members.items()):
            info = zipfile.ZipInfo(prefix + name, date_time=(2026, 10, 8, 0, 0, 0))
            info.create_system = 3
            mode = 0o755 if name.endswith('.command') else 0o644
            info.external_attr = (stat.S_IFREG | mode) << 16
            archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    with zipfile.ZipFile(destination) as archive:
        assert archive.testzip() is None
        names = {name[len(prefix):] for name in archive.namelist()}
        required = {'run.py', 'requirements.txt', 'docs/manual.html', 'static/app.js',
                    'weverse_bot/translations.py', 'weverse_bot/artist_comments.py', 'weverse_bot/comment_image.py', 'weverse_bot/post_adapter.py',
                    *PLATFORMS[platform]}
        assert required <= names
        for other in PLATFORMS:
            if other != platform:
                assert not set(PLATFORMS[other]) & names
        assert not any(set(Path(name).parts) & {'data', '.venv', '.git', 'attachments', '__pycache__'}
                       for name in names)
        for name in names:
            data = archive.read(prefix + name)
            if name.endswith('.py'):
                compile(data, name, 'exec')
            if name.endswith('.command'):
                assert (archive.getinfo(prefix + name).external_attr >> 16) & 0o777 == 0o755
            if name.endswith('.bat'):
                assert b'\n' not in data.replace(b'\r\n', b'')
    print(f'{destination.relative_to(ROOT)}: {destination.stat().st_size} bytes, SHA256 {hashlib.sha256(destination.read_bytes()).hexdigest()}')
    return members


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Build selected platform packages without private data')
    parser.add_argument('--platform', choices=['Windows', 'Mac', 'all'], default='all')
    selection = parser.parse_args().platform
    selected = list(PLATFORMS) if selection == 'all' else [selection]
    built = {platform: build(platform) for platform in selected}
    if len(built) == 2:
        win, mac = built['Windows'], built['Mac']
        shared = set(win) & set(mac)
        assert all(win[name] == mac[name] for name in shared)
        assert all(path.relative_to(ROOT).as_posix() in shared for path in common_files())
        print(f'OK: {len(shared)} shared files identical; CRC, Python syntax, launch files and permissions checked.')
    else:
        print(f'OK: {selection} only; CRC, Python syntax and platform launch files checked. Other packages unchanged.')

"""Single source of truth for exact command matching, permissions and help."""
COMMANDS = {
    'help': (1, 'help', '显示你能使用的指令'),
    '截图': (2, '截图 帖子链接', '仅返回原截图'),
    '烤制': (2, '烤制|帖子链接|\n正文译文\n+\n评论译文', '开头 + 补新评论；/e 引用表情；单独 /k 跳过该段译文'),
    '打开仓库': (2, '打开仓库', '查看本群待翻译与部分翻译档案'),
    '查看': (2, '查看 档案编号', '只返回原始截图'),
    '清空仓库': (2, '清空仓库', '永久删除本群全部档案、成图及历史译文'),
    '设置水印': (1, '设置水印 文字内容', '更新本群文字水印，不接收图片'),
    '查看水印': (1, '查看水印', '查看本群文字水印配置'),
    '调整水印': (1, '调整水印 位置1-9 字号 透明度0-100', '字号0跟随译文；颜色和描边在工作台设置'),
    '设置权限': (3, '设置权限 -l 1/2/3 @成员', '设置本群成员等级，也支持 --level'),
    '取消权限': (3, '取消权限 -l 当前等级 @成员', '移除本群成员权限，也支持 --level'),
}


def command_name(text):
    if text.startswith('烤制|'):
        return '烤制'
    parts = text.split(maxsplit=1)
    return parts[0] if parts and parts[0] in COMMANDS else None


def help_text(level):
    lines = [f'PLAVE 指令表 · 当前 level {level}', '不加 /、# 或 wv；每条消息只解析一次开头指令。']
    for name, (minimum, example, description) in COMMANDS.items():
        if level >= minimum:
            lines.append(f'{example}\n  {description}')
    lines.extend(['等级继承：1 水印；2 普通功能；3 权限管理。',
                  '烤制的中文由你提供，不是机器翻译；独立一行 + 分段，每个版块独立引用 /e；单独 /k 跳过译文但保留原文。',
                  '开头 + 为补充模式；每个链接只保留最后成功版本。旧格式 烤制 帖子链接 仍兼容。',
                  '清空仓库不可恢复，并暂停本群自动记录；水印、权限和登录不删除。'])
    return '\n'.join(lines)


def translations_from_body(body):
    import re
    if not body.strip():
        raise ValueError('请在链接后的下一行填写中文译文。')
    result, lines, key = {}, [], '0'
    for line in body.replace('\r\n', '\n').split('\n'):
        marker = re.fullmatch(r'\[(正文|评论\s*([1-9][0-9]*))\]', line.strip())
        if marker:
            if lines:
                result[key] = '\n'.join(lines).strip()
            key = marker.group(2) or '0'
            if key in result:
                raise ValueError('同一段译文的标记重复，请合并后再发送。')
            lines = []
        else:
            lines.append(line)
    if lines:
        result[key] = '\n'.join(lines).strip()
    return {k: v for k, v in result.items() if v}

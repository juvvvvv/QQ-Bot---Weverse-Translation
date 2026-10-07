"""One message, chronological blocks; latest-version reuse is identity based."""
import re
from . import store

MAX_BLOCKS = 201


def split_body(body):
    if not isinstance(body, str) or len(body) > 50000:
        raise ValueError('完整译文需为文字，最多 50000 字。')
    lines = body.replace('\r\n', '\n').replace('\r', '\n').strip().split('\n')
    append = bool(lines and lines[0].strip() == '+')
    if append:
        lines = lines[1:]
    blocks, current = [], []
    for line in lines:
        if line.strip() == '+':
            blocks.append('\n'.join(current).strip());current=[]
        else:
            current.append(line)
    blocks.append('\n'.join(current).strip())
    if not any(blocks):
        raise ValueError('请填写中文译文；补充模式在第一段前单独写一行 +。')
    if any(not block for block in blocks):
        raise ValueError('有空白译文段，请检查连续的 + 或末尾多出的 +。')
    if len(blocks) > MAX_BLOCKS:
        raise ValueError('单张图片最多 201 个译文版块。')
    return append, blocks


def identity(slot):
    return slot.get('comment_id') or ('main' if str(slot['key']) == '0' else 'legacy:' + str(slot['key']))


def latest_context(post):
    latest = store.latest_rendered(post['url'], post['group_id']) if post['url'] else None
    indexed = {identity(s): (s, latest['translations'].get(str(s['key']), '')) for s in latest['slots']} if latest else {}
    saved, changed = {}, []
    for slot in post['slots']:
        previous = indexed.get(identity(slot))
        if not previous or not previous[1]:
            continue
        if previous[0].get('text') == slot.get('text') and previous[0].get('author') == slot.get('author'):
            saved[str(slot['key'])] = previous[1]
        else:
            changed.append(str(slot['key']))
    return latest, saved, changed


def expand_emoji(text, slot):
    emojis = slot.get('emojis', [])
    index = 0
    def substitute(match):
        nonlocal index
        value = match.group()
        if value.startswith(('http://', 'https://')):
            return value
        if index >= len(emojis):
            raise ValueError(f"第 {int(slot['key'])+1} 段 /e 数量超过原文表情数量（{len(emojis)}）。请校对后重新烤制。")
        result=emojis[index];index+=1
        return result
    return re.sub(r'https?://\S+|/e(?![A-Za-z0-9_])', substitute, text)


def prepare(post, body):
    append, blocks = split_body(body)
    latest, saved, changed = latest_context(post)
    if append:
        if not latest:
            raise ValueError('本群没有这个链接的成功烤制存档，请先提交完整译文。')
        if changed:
            raise ValueError('已译原文发生变化，请核对并使用完整模式重新烤制；原存档已保留。')
        current_ids = {identity(s) for s in post['slots']}
        if any(identity(s) not in current_ids for s in latest['slots']):
            raise ValueError('本次页面未读到部分已存档评论，已停止补充，避免丢失旧译文。请展开评论后重试。')
        targets = [s for s in post['slots'] if str(s['key']) not in saved]
        if not targets:
            raise ValueError('当前没有待补译评论；修改错别字请使用完整模式重新烤制。')
        if any(str(s['key']) == '0' for s in targets):
            raise ValueError('正文尚无可复用译文，请使用完整模式烤制。')
    else:
        targets=post['slots'];saved={}
    if len(blocks) != len(targets):
        mode = '待补译评论' if append else '正文及艺人评论'
        raise ValueError(f'{mode}需要 {len(targets)} 段译文，实际提供 {len(blocks)} 段。请用单独一行 + 分隔；原存档不会被覆盖。')
    for slot, text in zip(targets, blocks):
        saved[str(slot['key'])] = expand_emoji(text, slot)
    return saved


def render_body(post_id, body):
    from .render import render_lock, render_post
    with render_lock:
        post=store.get_post(post_id)
        return render_post(post_id, prepare(post, body), publish=True)

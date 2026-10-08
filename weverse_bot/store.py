import json
import os
import re
import sqlite3
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get('WEVERSE_DATA_DIR', ROOT / 'data')).resolve()
DATA.mkdir(parents=True, exist_ok=True)
try:
    DATA.chmod(0o700)
except OSError:
    pass
for folder in ('originals', 'outputs', 'browser', 'logos'):
    (DATA / folder).mkdir(exist_ok=True)

DEFAULTS = {
    'owner_qq': '', 'groups': [], 'ws_url': 'ws://127.0.0.1:3001',
    'ws_token': '', 'font_path': '',
    'font_size': 24, 'capture_width': 720, 'capture_scale': 2, 'headless': True,
    'post_selector': '', 'text_selector': '', 'comment_selector': '',
    'comment_text_selector': '', 'artist_selector': '', 'author_selector': '',
    'expand_selector': '', 'feed_url': 'https://weverse.io/plave/artist',
    'artist_comment_count_selector': '.base-comment-artist-count-and-toggle-_-count',
    'capture_artist_comments': True,
    'feed_link_selector': '', 'monitor_enabled': False, 'poll_seconds': 300,
    'max_scrolls': 8, 'comment_wait_seconds': 30, 'monitor_paused_groups': [],
}


def db():
    con = sqlite3.connect(DATA / 'archive.sqlite3', timeout=20)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    con.execute('PRAGMA secure_delete=ON')
    return con


def init():
    with db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS members (group_id TEXT, user_id TEXT, PRIMARY KEY(group_id,user_id));
        CREATE TABLE IF NOT EXISTS posts (
            id TEXT PRIMARY KEY, url TEXT NOT NULL, title TEXT NOT NULL, group_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', original TEXT NOT NULL, output TEXT,
            slots TEXT NOT NULL, translations TEXT NOT NULL DEFAULT '{}', created REAL NOT NULL,
            updated REAL NOT NULL, source TEXT NOT NULL, note TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS memories (
            fingerprint TEXT PRIMARY KEY, text TEXT NOT NULL, updated REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS watermarks (group_id TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS post_files (
            post_id TEXT REFERENCES posts(id) ON DELETE CASCADE, path TEXT NOT NULL,
            PRIMARY KEY(post_id,path));
        CREATE TABLE IF NOT EXISTS garbage (path TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, level TEXT NOT NULL, message TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS latest_versions (
            group_id TEXT, url TEXT, post_id TEXT REFERENCES posts(id) ON DELETE CASCADE,
            PRIMARY KEY(group_id,url));
        ''')
        if 'level' not in {r['name'] for r in c.execute('PRAGMA table_info(members)')}:
            c.execute('ALTER TABLE members ADD COLUMN level INTEGER NOT NULL DEFAULT 2')
        c.execute('INSERT OR IGNORE INTO post_files SELECT id,original FROM posts')
        c.execute('INSERT OR IGNORE INTO post_files SELECT id,output FROM posts WHERE output IS NOT NULL')
        if c.execute('PRAGMA user_version').fetchone()[0] < 2:
            c.execute("UPDATE posts SET status='pending' WHERE status='ignored'")
            c.execute('PRAGMA user_version=2')
        c.execute('INSERT OR IGNORE INTO settings VALUES (1,?)', (json.dumps(DEFAULTS),))
    try:
        (DATA / 'archive.sqlite3').chmod(0o600)
    except OSError:
        pass


def settings():
    with db() as c:
        row = c.execute('SELECT value FROM settings WHERE id=1').fetchone()
    value = DEFAULTS | json.loads(row['value'])
    value.pop('comment_count_selector', None)
    return value


def save_settings(changes):
    value = settings() | changes
    with db() as c:
        c.execute('UPDATE settings SET value=? WHERE id=1', (json.dumps(value, ensure_ascii=False),))
    return value


def event(message, level='info'):
    # Tokens, cookies, incoming chat text and authentication URLs must never be logged.
    with db() as c:
        c.execute('INSERT INTO events(at,level,message) VALUES (?,?,?)', (time.time(), level, message))
        c.execute('DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT 200)')


def members():
    with db() as c:
        return [dict(r) for r in c.execute('SELECT * FROM members ORDER BY group_id,user_id')]


def permission_level(group, user):
    cfg = settings()
    if str(group) not in cfg['groups']:
        return 0
    if cfg['owner_qq'] and str(user) == cfg['owner_qq']:
        return 3
    with db() as c:
        row = c.execute('SELECT level FROM members WHERE group_id=? AND user_id=?',
                        (str(group), str(user))).fetchone()
    return row['level'] if row else 0


def authorized(group, user, minimum=2):
    return permission_level(group, user) >= minimum


def set_level(group, user, level):
    if type(level) is not int or level not in (1, 2, 3):
        raise ValueError('权限等级只支持 1、2、3。')
    if str(user) == settings()['owner_qq']:
        raise ValueError('主人权限由主人 QQ 设置决定，不能通过成员权限指令修改。')
    with db() as c:
        c.execute('INSERT INTO members(group_id,user_id,level) VALUES (?,?,?) '
                  'ON CONFLICT(group_id,user_id) DO UPDATE SET level=excluded.level',
                  (str(group), str(user), level))


def revoke_level(group, user, expected_level=None):
    if str(user) == settings()['owner_qq']:
        raise ValueError('不能取消主人的权限。')
    with db() as c:
        row = c.execute('SELECT level FROM members WHERE group_id=? AND user_id=?',
                        (str(group), str(user))).fetchone()
        if not row:
            raise ValueError('该成员尚未获得本群权限。')
        if expected_level is not None and row['level'] != expected_level:
            raise ValueError(f"该成员当前为 level {row['level']}，请使用对应等级取消权限。")
        c.execute('DELETE FROM members WHERE group_id=? AND user_id=?', (str(group), str(user)))


def allow(group, user, enabled=True):
    # Compatibility for v1 local callers: previous translation members migrate to level 2.
    if enabled:
        set_level(group, user, 2)
    else:
        revoke_level(group, user)


def unpack(row):
    value = dict(row)
    for key in ('slots', 'translations'):
        value[key] = json.loads(value[key])
    return value


def posts(status='', group=None):
    query, args = 'SELECT * FROM posts WHERE 1=1', []
    if status:
        query += ' AND status=?'
        args.append(status)
    if group is not None:
        query += ' AND group_id=?'
        args.append(str(group))
    with db() as c:
        return [unpack(r) for r in c.execute(query + ' ORDER BY created DESC LIMIT 200', args)]


def get_post(post_id, group=None):
    with db() as c:
        row = c.execute('SELECT * FROM posts WHERE id=?', (post_id,)).fetchone()
    if row is None or (group is not None and row['group_id'] != str(group)):
        raise ValueError('找不到本群的档案，请检查编号。')
    return unpack(row)


def memory(fingerprint, group=""):
    fingerprint = str(group) + "|" + fingerprint
    with db() as c:
        row = c.execute('SELECT text FROM memories WHERE fingerprint=?', (fingerprint,)).fetchone()
    return row['text'] if row else None


def add_post(url, title, group, original, slots, source, note=''):
    post_id = uuid.uuid4().hex[:10]
    now = time.time()
    with db() as c:
        c.execute('''INSERT INTO posts
            (id,url,title,group_id,original,slots,created,updated,source,note)
            VALUES (?,?,?,?,?,?,?,?,?,?)''',
            (post_id, url, title, str(group), original, json.dumps(slots, ensure_ascii=False), now, now, source, note))
        c.execute('INSERT INTO post_files VALUES (?,?)', (post_id, original))
    return get_post(post_id)


def update_post(post_id, **changes):
    if not set(changes) <= {'translations', 'output', 'status', 'note'}:
        raise ValueError('不支持的档案字段')
    values = []
    for key, value in changes.items():
        values.append(json.dumps(value, ensure_ascii=False) if key == 'translations' else value)
    with db() as c:
        c.execute('UPDATE posts SET ' + ','.join(f'{key}=?' for key in changes) + ',updated=? WHERE id=?',
                  values + [time.time(), post_id])
        if changes.get('output'):
            c.execute('INSERT OR IGNORE INTO post_files VALUES (?,?)', (post_id, changes['output']))
    return get_post(post_id)


def remember(fingerprint, text, group=""):
    fingerprint = str(group) + "|" + fingerprint
    with db() as c:
        c.execute('INSERT OR REPLACE INTO memories VALUES (?,?,?)', (fingerprint, text, time.time()))


init()


def latest_by_url(url, group):
    with db() as c:
        row = c.execute('SELECT * FROM posts WHERE url=? AND group_id=? ORDER BY created DESC LIMIT 1',
                        (url, str(group))).fetchone()
    return unpack(row) if row else None


def latest_rendered(url, group):
    with db() as c:
        row = c.execute('''SELECT p.* FROM latest_versions v JOIN posts p ON p.id=v.post_id
                           WHERE v.url=? AND v.group_id=? AND p.group_id=v.group_id AND p.url=v.url''', (url, str(group))).fetchone()
        if row is None:
            # Import existing successful v2 images lazily without touching them
            # until the first successful new bake is published.
            row = c.execute('''SELECT * FROM posts WHERE url=? AND group_id=? AND output IS NOT NULL
                               ORDER BY updated DESC,created DESC LIMIT 1''', (url, str(group))).fetchone()
    return unpack(row) if row else None


def publish_latest(post_id, translations, output, status):
    """Swap only after PNG is saved. Retire older versions within this group/URL."""
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        post = unpack(c.execute('SELECT * FROM posts WHERE id=?', (post_id,)).fetchone())
        old_ids = [post_id]
        if post['url']:
            old_ids += [r['id'] for r in c.execute('SELECT id FROM posts WHERE group_id=? AND url=? AND id!=?',
                                                  (post['group_id'], post['url'], post_id))]
        candidates = set()
        for old_id in old_ids:
            candidates.update(r['path'] for r in c.execute('SELECT path FROM post_files WHERE post_id=?', (old_id,)))
        c.execute('UPDATE posts SET translations=?,output=?,status=?,updated=? WHERE id=?',
                  (json.dumps(translations, ensure_ascii=False), output, status, time.time(), post_id))
        c.execute('DELETE FROM post_files WHERE post_id=?', (post_id,))
        for path in (post['original'], output):
            c.execute('INSERT OR IGNORE INTO post_files VALUES (?,?)', (post_id, path))
        for old_id in old_ids[1:]:
            c.execute('DELETE FROM posts WHERE id=?', (old_id,))
        if post['url']:
            c.execute('INSERT OR REPLACE INTO latest_versions VALUES (?,?,?)', (post['group_id'], post['url'], post_id))
        retained = {r['path'] for r in c.execute('SELECT path FROM post_files')}
        for path in candidates - retained:
            c.execute('INSERT OR IGNORE INTO garbage VALUES (?)', (path,))
    try:
        cleanup_garbage()
    except OSError:
        event('最新烤制已保存；旧图片清理待重试，请检查文件权限。', 'warning')
    return get_post(post_id)


WATERMARK_DEFAULTS = {'text': '@PLAVE_PixelDiary', 'enabled': True, 'position': 8,
                      'font_size': 0, 'color': '#000000', 'outline': False,
                      'outline_color': '#ffffff', 'outline_width': 1, 'transparency': 80}


def watermark(group=''):
    with db() as c:
        row = c.execute('SELECT value FROM watermarks WHERE group_id=?', (str(group),)).fetchone()
        if row is None and group:
            row = c.execute("SELECT value FROM watermarks WHERE group_id='' ").fetchone()
    raw = json.loads(row['value']) if row else {}
    if 'text' in raw:
        return WATERMARK_DEFAULTS | {k: v for k, v in raw.items() if k in WATERMARK_DEFAULTS}
    # Old files are retained, but their PNG logos are no longer rendered.
    legacy_text = settings().get('watermark', '@PLAVE_PixelDiary')
    value = dict(WATERMARK_DEFAULTS)
    if legacy_text != 'PLAVE · 中文翻译':
        value.update(text=legacy_text, enabled=bool(legacy_text))
    if raw:
        positions = {'top-left': 1, 'top-right': 3, 'bottom-left': 7, 'bottom-right': 9, 'footer': 5}
        value['position'] = positions.get(raw.get('position'), 5)
    return value


def validate_watermark(value):
    if set(value) - set(WATERMARK_DEFAULTS):
        raise ValueError('水印设置包含未知字段；只支持文字水印。')
    for key, lo, hi in (('position', 1, 9), ('font_size', 0, 96), ('transparency', 0, 100), ('outline_width', 0, 8)):
        if type(value[key]) is not int or not lo <= value[key] <= hi:
            raise ValueError(f'{key} 必须在 {lo}–{hi} 范围内。')
    if value['font_size'] != 0 and value['font_size'] < 8:
        raise ValueError('水印字号为 0 时跟随译文，手动字号需为 8–96。')
    for key in ('enabled', 'outline'):
        if type(value[key]) is not bool:
            raise ValueError(f'{key} 需为布尔值。')
    for key in ('color', 'outline_color'):
        if not isinstance(value[key], str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', value[key]):
            raise ValueError('水印颜色需为六位十六进制颜色，例如 #ffffff。')
    if not isinstance(value['text'], str) or len(value['text']) > 200:
        raise ValueError('水印文字最多 200 字。')
    return value


def save_watermark(group, changes):
    value = validate_watermark(watermark(group) | changes)
    with db() as c:
        c.execute('INSERT OR REPLACE INTO watermarks VALUES (?,?)', (str(group), json.dumps(value)))
    return value


def cleanup_garbage():
    removed = 0
    with db() as c:
        paths = [r['path'] for r in c.execute('SELECT path FROM garbage')]
    for name in paths:
        path = (DATA / name).resolve()
        if path.parent not in ((DATA / 'originals').resolve(), (DATA / 'outputs').resolve()):
            raise ValueError('图片清理路径异常，未删除该文件。')
        path.unlink(missing_ok=True)
        with db() as c:
            c.execute('DELETE FROM garbage WHERE path=?', (name,))
        removed += 1
    return removed


def clear_warehouse(group):
    """Erase all group archives/memory, all output revisions, and unshared originals."""
    from .render import render_lock
    with render_lock:
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            rows = list(c.execute('SELECT id,original,output FROM posts WHERE group_id=?', (str(group),)))
            ids = {r['id'] for r in rows}
            candidates = {r['original'] for r in rows} | {r['output'] for r in rows if r['output']}
            candidates |= {r['path'] for r in c.execute(
                'SELECT path FROM post_files JOIN posts ON posts.id=post_files.post_id WHERE posts.group_id=?', (str(group),))}
            # Include legacy v1 revisions which were created before the file ledger existed.
            for post_id in ids:
                candidates |= {str(p.relative_to(DATA)) for p in (DATA / 'outputs').glob(post_id + '-*.png')}
            retained = {r['original'] for r in c.execute('SELECT original FROM posts WHERE group_id<>?', (str(group),))}
            retained |= {r['output'] for r in c.execute('SELECT output FROM posts WHERE group_id<>? AND output IS NOT NULL', (str(group),))}
            retained |= {r['path'] for r in c.execute(
                'SELECT path FROM post_files JOIN posts ON posts.id=post_files.post_id WHERE posts.group_id<>?', (str(group),))}
            for name in candidates - retained:
                c.execute('INSERT OR IGNORE INTO garbage VALUES (?)', (name,))
            prefix = str(group) + '|'
            erased_memories = c.execute('DELETE FROM memories WHERE substr(fingerprint,1,?)=?', (len(prefix), prefix)).rowcount
            c.execute('DELETE FROM posts WHERE group_id=?', (str(group),))
            current = json.loads(c.execute('SELECT value FROM settings WHERE id=1').fetchone()['value'])
            current['monitor_paused_groups'] = list(dict.fromkeys(current.get('monitor_paused_groups', []) + [str(group)]))
            c.execute('UPDATE settings SET value=? WHERE id=1', (json.dumps(current, ensure_ascii=False),))
        try:
            removed = cleanup_garbage()
        except OSError as exc:
            raise ValueError('档案及历史译文已删除，但部分图片删除失败。检查文件权限后再次清空仓库；不会报告全部清理成功。') from exc
        event(f'本群仓库已永久清空：{len(rows)} 个档案；该群自动记录已暂停')
        return {'posts': len(rows), 'memories': erased_memories, 'files': removed, 'monitor_paused': True}

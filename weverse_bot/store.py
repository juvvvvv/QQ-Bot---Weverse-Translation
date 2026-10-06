import json
import os
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
    'ws_token': '', 'watermark': 'PLAVE · 中文翻译', 'font_path': '',
    'font_size': 24, 'capture_width': 720, 'headless': True,
    'post_selector': '', 'text_selector': '', 'comment_selector': '',
    'comment_text_selector': '', 'artist_selector': '', 'author_selector': '',
    'expand_selector': '', 'feed_url': 'https://weverse.io/plave/artist',
    'feed_link_selector': '', 'monitor_enabled': False, 'poll_seconds': 300,
    'max_scrolls': 8, 'monitor_paused_groups': [],
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
    return DEFAULTS | json.loads(row['value'])


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


WATERMARK_DEFAULTS = {'logo': None, 'enabled': True, 'position': 'footer',
                      'width_pct': 18, 'opacity': 70, 'margin': 16}


def watermark(group=''):
    with db() as c:
        row = c.execute('SELECT value FROM watermarks WHERE group_id=?', (str(group),)).fetchone()
    return WATERMARK_DEFAULTS | (json.loads(row['value']) if row else {})


def save_watermark(group, changes):
    value = watermark(group) | changes
    if value['position'] not in ('footer', 'top-left', 'top-right', 'bottom-left', 'bottom-right'):
        raise ValueError('水印位置支持：底部、左上、右上、左下、右下。')
    for key, lo, hi in (('width_pct', 5, 50), ('opacity', 0, 100), ('margin', 0, 100)):
        if type(value[key]) is not int or not lo <= value[key] <= hi:
            raise ValueError(f'{key} 必须在 {lo}–{hi} 范围内。')
    if type(value['enabled']) is not bool:
        raise ValueError('水印启用状态需为布尔值。')
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

"""Independent OneBot v11 client; no upstream source code is copied."""
import asyncio
import base64
import json
import re
import time
import uuid
from collections import OrderedDict
import websockets
from . import store
from .capture import browser
from .render import render_post

from .commands import COMMANDS, command_name, help_text, translations_from_body
from .locks import workflow_lock
from .translations import render_body, split_body


class QQClient:
    def __init__(self):
        self.ws = None
        self.task = None
        self.worker_task = None
        self.enabled = False
        self.status = '未连接'
        self.account = None
        self.pending = {}
        self.queue = asyncio.Queue(maxsize=20)
        self.seen = OrderedDict()
        self.rate = {}
        self.generation = 0

    async def start(self):
        await self.stop()
        self.enabled = True
        self.generation += 1
        self.worker_task = asyncio.create_task(self.worker())
        self.task = asyncio.create_task(self.run())

    async def stop(self):
        self.enabled = False
        for task in (self.task, self.worker_task):
            if task:
                task.cancel()
        await asyncio.gather(*(t for t in (self.task, self.worker_task) if t), return_exceptions=True)
        self.task = self.worker_task = None
        if self.ws:
            await self.ws.close()
            self.ws = None
        for future in self.pending.values():
            if not future.done():
                future.set_exception(ValueError('QQ 连接已关闭'))
        self.pending.clear()
        self.account = None
        self.status = '未连接'
        while not self.queue.empty():
            self.queue.get_nowait()
            self.queue.task_done()

    async def call(self, action, params):
        if not self.ws:
            raise ValueError('QQ 未连接。请在 WebUI 设置中连接 OneBot。')
        echo = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self.pending[echo] = future
        try:
            await self.ws.send(json.dumps({'action': action, 'params': params, 'echo': echo}))
            response = await asyncio.wait_for(future, timeout=20)
            if response.get('status') != 'ok' or response.get('retcode', 0) != 0:
                raise ValueError(f"OneBot 拒绝操作（retcode={response.get('retcode', '未知')}）。请检查 QQ 登录及群权限。")
            return response.get('data')
        finally:
            self.pending.pop(echo, None)

    async def send(self, group, text='', image=None):
        message = []
        if text:
            message.append({'type': 'text', 'data': {'text': text}})
        if image:
            data = (store.DATA / image).read_bytes()
            if len(data) > 16 * 1024 * 1024:
                raise ValueError('图片超过 16 MB，请在 WebUI 下载后分图发送。')
            message.append({'type': 'image', 'data': {'file': 'base64://' + base64.b64encode(data).decode()}})
        return await self.call('send_group_msg', {'group_id': int(group), 'message': message})

    async def identify(self):
        try:
            result = await self.call('get_login_info', {})
            self.account = str(result['user_id'])
            self.status = f'已连接 · QQ {self.account}'
            store.event('OneBot 连接成功，已验证 QQ 登录状态')
        except Exception:
            self.status = 'WebSocket 已连接，但 QQ 登录未验证'
            store.event('OneBot 登录信息读取失败，请检查 QQ 登录', 'warning')

    async def run(self):
        retry = 2
        while self.enabled:
            cfg = store.settings()
            headers = {'Authorization': 'Bearer ' + cfg['ws_token']} if cfg['ws_token'] else {}
            identity = None
            try:
                self.status = '正在连接 OneBot…'
                async with websockets.connect(cfg['ws_url'], additional_headers=headers,
                                              open_timeout=10, max_size=6 * 1024 * 1024) as ws:
                    self.ws = ws
                    retry = 2
                    identity = asyncio.create_task(self.identify())
                    async for raw in ws:
                        payload = json.loads(raw)
                        if not isinstance(payload, dict):
                            continue
                        if payload.get('echo') in self.pending:
                            future = self.pending[payload['echo']]
                            if not future.done():
                                future.set_result(payload)
                        elif payload.get('post_type') == 'message' and payload.get('message_type') == 'group':
                            text = self.text(payload)
                            if command_name(text):
                                message_id = str(payload.get('message_id', uuid.uuid4()))
                                key = (str(payload.get('group_id')), message_id)
                                if key in self.seen:
                                    continue
                                self.seen[key] = time.monotonic()
                                while len(self.seen) > 1000:
                                    self.seen.popitem(last=False)
                                if not self.queue.full():
                                    self.queue.put_nowait((payload, self.generation))
                                else:
                                    store.event('QQ 指令队列已满，丢弃新任务；请稍后重试', 'warning')
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.status = f'连接失败（{type(exc).__name__}），{retry} 秒后重试'
            finally:
                if identity:
                    identity.cancel()
                    await asyncio.gather(identity, return_exceptions=True)
                self.ws = None
                self.account = None
                for future in self.pending.values():
                    if not future.done():
                        future.set_exception(ValueError('QQ 连接中断'))
            await asyncio.sleep(retry)
            retry = min(retry * 2, 30)

    @staticmethod
    def text(event):
        message = event.get('message', [])
        if isinstance(message, list):
            return ''.join(str(s.get('data', {}).get('text', '')) for s in message if s.get('type') == 'text').strip()
        return re.sub(r'\[CQ:[^\]]*\]', '', str(message)).strip()

    async def worker(self):
        while True:
            event, generation = await self.queue.get()
            try:
                if generation == self.generation:
                    await self.handle(event)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                group = str(event.get('group_id', ''))
                store.event(f'QQ 指令失败（{type(exc).__name__}）', 'warning')
                try:
                    await self.send(group, str(exc) if isinstance(exc, ValueError) else '处理失败，请到 WebUI 查看状态。')
                except Exception:
                    store.event('QQ 错误提示发送失败，请检查连接', 'warning')
            finally:
                self.queue.task_done()

    @staticmethod
    def mentions(event):
        segments = event.get('message', [])
        if isinstance(segments, list):
            return [str(s.get('data', {}).get('qq', '')) for s in segments if s.get('type') == 'at']
        # Legacy OneBot string messages carry real CQ at segments, not plain nickname text.
        return re.findall(r'\[CQ:at,qq=([1-9][0-9]{4,19})(?:,[^\]]*)?\]', str(segments))

    async def handle(self, event):
        group, user = str(event.get('group_id', '')), str(event.get('user_id', ''))
        text = self.text(event)
        command = command_name(text)
        if not command or group not in store.settings()['groups']:
            return
        minimum = COMMANDS[command][0]
        level = store.permission_level(group, user)
        if level < minimum:
            key = (group, user)
            if time.monotonic() - self.rate.get(key, 0) > 30:
                self.rate[key] = time.monotonic()
                if len(self.rate) > 2000:
                    self.rate = {key: self.rate[key]}
                await self.send(group, f'权限不足：此指令需要 level {minimum}，你当前为 level {level}。请联系主人或 level 3 管理员。')
            return
        if command == 'help':
            if text != 'help':
                raise ValueError('查看指令请单独发送 help。')
            await self.send(group, help_text(level))
            return
        if command in ('设置权限', '取消权限'):
            match = re.fullmatch(r'(设置权限|取消权限)\s+(?:-l|--level)\s+([123])', text)
            users = self.mentions(event)
            if not match or not users or any(not re.fullmatch(r'[1-9][0-9]{4,19}', u) for u in users):
                raise ValueError('格式：设置权限 -l 1/2/3 @成员；取消权限 -l 当前等级 @成员。需要真正 @ 成员，不能手写昵称。')
            users = list(dict.fromkeys(users))
            if len(users) != 1:
                raise ValueError('每次只设置或取消一个成员的权限。')
            chosen = int(match.group(2))
            if command == '设置权限':
                store.set_level(group, users[0], chosen)
                reply = f'已设置 @{users[0]} 为本群 level {chosen}。'
            else:
                store.revoke_level(group, users[0], chosen)
                reply = f'已移除 @{users[0]} 的本群权限。'
            await self.send(group, reply)
            return
        async with workflow_lock:
            # Check again after waiting: WebUI can revoke permissions while this task is queued.
            if not store.authorized(group, user, minimum):
                raise ValueError('你的权限已变更，无法执行此指令。')
            if command in ('截图', '烤制'):
                first, separator, body = text.replace('\r\n', '\n').partition('\n')
                pipe = command == '烤制' and first.startswith('烤制|')
                if pipe:
                    match = re.fullmatch(r'烤制\|([^|]+)\|\s*', first)
                    header = ['烤制', match[1].strip()] if match else []
                else:
                    header = first.split()
                if len(header) != 2 or (command == '截图' and body.strip()):
                    raise ValueError('格式：截图 帖子链接；烤制 帖子链接，然后换行填写译文。')
                if pipe:
                    split_body(body)  # Validate before doing network work.
                translations = translations_from_body(body) if command == '烤制' and not pipe else None
                await self.send(group, '开始读取帖子，请稍等。')
                saved = await browser.capture(header[1], group)
                if not store.authorized(group, user, minimum):
                    raise ValueError('你的权限已取消，任务不再发图。')
                if command == '截图':
                    await self.send(group, f"档案 {saved['id']} · 原始截图\n{saved['note']}", saved['original'])
                else:
                    result = await asyncio.to_thread(render_body, saved['id'], body) if pipe else await asyncio.to_thread(render_post, saved['id'], translations, True)
                    if not store.authorized(group, user, minimum):
                        raise ValueError('你的权限已取消，任务不再发图。')
                    await self.send(group, f"档案 {saved['id']} · {'全部完成' if result['status']=='translated' else '部分翻译，仍有未译评论'}", result['output'])
            elif command == '查看':
                parts = text.split()
                if len(parts) != 2:
                    raise ValueError('格式：查看 档案编号')
                saved = store.get_post(parts[1], group)
                await self.send(group, f"档案 {saved['id']} · 原始截图", saved['original'])
            elif command == '打开仓库':
                if text != command:
                    raise ValueError('请单独发送 打开仓库。')
                entries = [p for p in store.posts(group=group) if p['status'] in ('pending', 'partial')]
                await self.send(group, '本群未完成档案（最近200条中的前20条）：\n' +
                                ('\n'.join(f"{p['id']} · {p['title'][:35]}" for p in entries[:20]) or '暂无'))
            elif command == '清空仓库':
                if text != command:
                    raise ValueError('请单独发送 清空仓库；不接受额外参数。')
                result = await asyncio.to_thread(store.clear_warehouse, group)
                await self.send(group, f"已永久删除本群 {result['posts']} 个档案及 {result['memories']} 条历史译文，不可恢复。\n水印和成员权限保留；本群自动记录已暂停，可由主人在 WebUI 恢复。")
            elif command == '设置水印':
                parts = text.split(maxsplit=1)
                if len(parts) != 2:
                    raise ValueError('格式：设置水印 文字内容；水印只支持文字。')
                store.save_watermark(group, {'text': parts[1], 'enabled': True})
                await self.send(group, '本群文字水印已保存，将在正文和评论合成后最后添加。')
            elif command == '调整水印':
                match = re.fullmatch(r'调整水印\s+([1-9])\s+(\d+)\s+(\d+)', text)
                if not match:
                    raise ValueError('格式：调整水印 位置1-9 字号 透明度0-100；字号0跟随译文。')
                store.save_watermark(group, {'position': int(match[1]), 'font_size': int(match[2]),
                                            'transparency': int(match[3])})
                await self.send(group, '文字水印位置、字号和透明度已保存。')
            elif command == '查看水印':
                if text != command:
                    raise ValueError('请单独发送 查看水印。')
                cfg = store.watermark(group)
                await self.send(group, f"本群文字水印：{cfg['text']}\n位置：{cfg['position']}；字号：{cfg['font_size'] or '跟随译文'}；透明度：{cfg['transparency']}%\n颜色：{cfg['color']}；描边：{'启用' if cfg['outline'] else '关闭'}")


qq = QQClient()

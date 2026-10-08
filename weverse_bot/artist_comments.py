"""Capture the artist-only list, keeping comment identity separate from order."""
import json
import math
import time
from datetime import datetime, timezone

LIST = '.comment-list-by-artists-_-comment_list'
CARD = '.comment-item-_-container'
TEXT = '.comment-item-content-_-comment .line-clamp-node-view-_-container'
AUTHOR = '.comment-item-header-profile-name-_-name'
TIME = '.comment-item-header-_-time'
BADGE = 'svg g[id="24/em/ic_officialbadge_special_medium"]'
TOGGLE = '.base-comment-artist-count-and-toggle-_-toggle_button'


def machine_time(value):
    """Read an explicit machine date; localized display text is never a date key."""
    if value is None or value == '':
        return None
    try:
        result = float(value)
        if result > 100_000_000_000:
            result /= 1000  # Unix milliseconds.
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        try:
            result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
            return result.replace(tzinfo=result.tzinfo or timezone.utc).timestamp()
        except ValueError:
            return None


def order_comments(records):
    ids = {r['comment_id']: r for r in records}
    # Only use a supplied, unambiguous parent ID. Names and numeric ID order
    # cannot establish a thread relationship.
    children, roots = {}, []
    for record in records:
        parent = record.get('parent_id')
        seen = {record['comment_id']}
        current = parent
        while current in ids and current not in seen:
            seen.add(current)
            current = ids[current].get('parent_id')
        if current in seen:
            parent = None
            record['parent_id'] = None
        if parent in ids:
            children.setdefault(parent, []).append(record)
        else:
            roots.append(record)
    # The supplied Weverse list is newest first, including same-minute replies.
    # Use one consistent ordering for the whole snapshot. Never mix parsed dates
    # with unrecognized localized strings, or infer chronology from comment IDs.
    timestamps = {r['comment_id']: machine_time(r.get('timestamp')) for r in records}
    if records and all(value is not None for value in timestamps.values()):
        key = lambda r: (timestamps[r['comment_id']], -r['source_index'])
    else:
        key = lambda r: -r['source_index']
    result = []
    def visit(record, depth):
        record['depth'] = max(depth, 1 if record['is_reply'] else 0)
        record['grouping_known'] = not record['is_reply'] or record.get('parent_id') in ids
        result.append(record)
        for child in sorted(children.get(record['comment_id'], []), key=key):
            visit(child, depth + 1)
    for record in sorted(roots, key=key):
        visit(record, 0)
    return result


async def ordinary_region_snapshot(page):
    """Evidence for zero artist comments from the supplied ordinary-only DOM."""
    if await page.locator('.base-comment-artist-count-and-toggle-_-container,' + LIST).count():
        return None
    candidates=page.locator('.comment-total-count-and-refresh-_-container')
    snapshots=[]
    from .capture_layout import parse_count
    for i in range(await candidates.count()):
        header=candidates.nth(i)
        if not await header.is_visible():continue
        snapshot=await header.evaluate(r'''el=>{
            const region=el.closest('.comment-shape-by-item-type-_-container');
            const list=region?.querySelector('.wrap_comment_list');
            const visible=n=>n.getClientRects().length && getComputedStyle(n).display!=='none' && getComputedStyle(n).visibility!=='hidden';
            if(!region || !list || !visible(list))return null;
            if(region.matches('[aria-busy="true"]') || [...region.querySelectorAll('[aria-busy="true"],[role="progressbar"],[class*="skeleton"],[class*="loading"]')].some(visible))return null;
            const cards=[...list.querySelectorAll('.comment-item-_-container')];
            if(cards.some(c=>c.querySelector('svg g[id="24/em/ic_officialbadge_special_medium"]')))return null;
            if(cards.some(c=>!c.getAttribute('data-wev-comment-id') || !c.querySelector('.comment-item-header-profile-name-_-name')?.textContent.trim() || !c.querySelector('.line-clamp-node-view-_-container')?.textContent.trim()))return null;
            return {count:el.querySelector('.comment-total-count-and-refresh-_-count')?.textContent,
                    ids:cards.map(c=>c.getAttribute('data-wev-comment-id')),text:list.textContent};
        }''')
        if snapshot:
            count=parse_count(snapshot['count'] or '')
            if count and not count['approximate'] and (snapshot['ids'] or count['value']==0):snapshots.append(snapshot)
    return json.dumps(snapshots[0],sort_keys=True) if len(snapshots)==1 else None


async def zero_artist_snapshot(page, cfg):
    """Recognize no artist comments on loaded originals, including empty regions."""
    ordinary=await ordinary_region_snapshot(page)
    if ordinary:return 'ordinary:'+ordinary
    # An empty title/list may remain even when there are no artist comments.
    # Artist card markup with an unreadable counter must still stop.
    if await page.locator(CARD+' '+BADGE).count():
        return None
    root=page.locator(cfg['post_selector'])
    if await root.count()!=1 or not await root.is_visible():return None
    text=root.locator(cfg['text_selector'])
    author=root.locator(cfg['author_selector'])
    if await text.count()!=1 or await author.count()!=1 or not await text.is_visible():return None
    original=(await text.inner_text()).strip()
    if not original or not (await author.text_content() or '').strip():return None
    state=await page.evaluate(r'''()=>{
        const visible=n=>n.getClientRects().length && getComputedStyle(n).display!=='none' && getComputedStyle(n).visibility!=='hidden';
        const regions=[...document.querySelectorAll('.comment-shape-by-item-type-_-container,.community-artist-postId-_-aside,.community-fanpost-postId-_-aside,.base-comment-artist-count-and-toggle-_-container,.comment-list-by-artists-_-comment_list')];
        const busy='[aria-busy="true"],[role="progressbar"],[class*="skeleton"],[class*="loading"]';
        if(document.readyState==='loading' || document.body.getAttribute('aria-busy')==='true' ||
           document.documentElement.getAttribute('aria-busy')==='true' ||
           regions.some(el=>el.matches('[aria-busy="true"]') || [...el.querySelectorAll(busy)].some(visible)))return null;
        return regions.filter(visible).map(el=>el.innerText);
    }''')
    if state is None:return None
    return 'absent:'+json.dumps({'original':original,'regions':state},sort_keys=True)


async def collect_artist_comments(page, cfg, api_parents=None):
    """Wait for the artist region, not a fixed sleep or one empty DOM lookup."""
    from .capture_layout import read_comment_counts
    from .page_cleanup import reject_optional_consent
    page._wv_comment_zero = None
    page._wv_native_ready = False
    deadline = time.monotonic() + cfg.get('comment_wait_seconds', 30)
    listing = None
    raw = []
    expected = None
    clicked = False
    stable_key = None
    stable_since = 0
    last_scroll = 0
    scrolls = 0
    problem = ''
    while True:
        await reject_optional_consent(page)
        counts = await read_comment_counts(page, cfg)
        count = counts['artist']
        expected = count['value'] if count else None
        if expected is not None and expected > 200:
            raise ValueError('艺人评论超过单张图片的 200 条上限，请分批处理。')
        containers = page.locator(LIST)
        visible = [containers.nth(i) for i in range(await containers.count())
                   if await containers.nth(i).is_visible()]
        if len(visible) > 1:
            raise ValueError('匹配到多个可见艺人评论列表，请检查页面。')
        listing = visible[0] if visible else None
        zero_snapshot = await zero_artist_snapshot(page,cfg) if count is None else None
        ordinary_header = await page.locator('.comment-total-count-and-refresh-_-container').count()
        # Preserve explicitly configured legacy adapters. The default native
        # path must never interpret a missing list/counter as zero comments.
        if not await containers.count() and count is None and not ordinary_header and cfg.get('comment_selector'):
            legacy = page.locator(cfg['comment_selector'])
            if await legacy.count():
                return []
        buttons = page.locator(TOGGLE)
        if not clicked and expected != 0:
            for i in range(min(await buttons.count(), 5)):
                button = buttons.nth(i)
                if await button.is_visible() and await button.get_attribute('aria-expanded') == 'false':
                    await button.click(timeout=3000)
                    clicked = True
                    break
        raw = []
        busy = False
        if listing is not None:
            raw, busy = await listing.evaluate(r'''el => {
                const cards=[...el.children].filter(n=>n.matches('.comment-item-_-container'));
                const result=[];
                for(let index=0;index<cards.length;index++) {
                    const c=cards[index];
                    if(!c.querySelector('.comment-item-header-_-container svg g[id="24/em/ic_officialbadge_special_medium"]'))continue;
                    const t=c.querySelectorAll('.comment-item-header-_-time');
                    const date=t[0];
                    const stamp=date?.getAttribute('datetime') || date?.getAttribute('data-timestamp')
                        || date?.getAttribute('data-created-at') || date?.querySelector('time[datetime]')?.getAttribute('datetime');
                    const author=c.querySelector('.comment-item-header-profile-name-_-name');
                    const text=c.querySelector('.comment-item-content-_-comment .line-clamp-node-view-_-container');
                    result.push({comment_id:c.getAttribute('data-wev-comment-id'),
                        parent_id:c.getAttribute('data-parent-comment-id') || c.getAttribute('data-root-comment-id'),
                        is_reply:c.classList.contains('comment-item-_--instant-reply'),
                        published:date?.innerText?.trim() || '',timestamp:stamp,source_index:index,
                        ready:!!(t.length===1 && date?.innerText?.trim() && author?.textContent?.trim() && text),
                        snapshot:(author?.textContent || '')+'\n'+(text?.textContent || '')});
                }
                const region=el.closest('.comment-shape-by-item-type-_-container') || el;
                const busy=region?.getAttribute('aria-busy')==='true' || !!region?.querySelector('[aria-busy="true"], [role="progressbar"]');
                return [result,busy];
            }''')
        ids = [r['comment_id'] for r in raw]
        valid = all(ids) and len(ids) == len(set(ids)) and all(r['ready'] for r in raw)
        ready = count is not None and not count['approximate'] and expected == len(raw) and valid and not busy
        # An explicit, stable zero is different from a missing counter.
        if count is not None and not count['approximate'] and expected == 0 and not raw and not busy:
            ready = True
        if zero_snapshot:
            expected = 0
            ready = True
        if ready:
            key = (expected, zero_snapshot, tuple((r['comment_id'], r['published'], r['snapshot']) for r in raw))
            if key != stable_key:
                stable_key, stable_since = key, time.monotonic()
            absent_wait=min(5.0,max(3.0,cfg.get('comment_wait_seconds',30)-.5))
            wait = (absent_wait if zero_snapshot and zero_snapshot.startswith('absent:') else
                    2.0 if zero_snapshot else 1.0 if expected == 0 else .4)
            if time.monotonic() - stable_since >= wait:
                if zero_snapshot:page._wv_comment_zero = zero_snapshot
                page._wv_native_ready = True
                break
        else:
            stable_key = None
        if count is None:
            problem = '未识别艺人评论计数'
        elif count['approximate']:
            problem = '艺人评论数为近似值，无法确认完整数量'
        elif not valid:
            problem = '评论编号、作者或发布时间尚未完整加载'
        else:
            problem = f'网页显示 {expected} 条艺人评论，本次仅读取 {len(raw)} 条'
        if time.monotonic() >= deadline:
            raise ValueError(f'艺人评论加载等待超时：{problem}。已停止截图，原存档保留；请检查评论区和网络后重试。')
        if listing is not None and not ready and scrolls < cfg['max_scrolls'] and time.monotonic() - last_scroll >= .8:
            cards = listing.locator(':scope > ' + CARD)
            if await cards.count():
                await cards.last.scroll_into_view_if_needed(timeout=2000)
            await listing.evaluate('''el => {
                for(let n=el;n;n=n.parentElement) {
                    if(n.scrollHeight>n.clientHeight+4 && /auto|scroll/.test(getComputedStyle(n).overflowY)) {
                        n.scrollTop=n.scrollHeight;return;
                    }
                }
            }''')
            scrolls += 1
            last_scroll = time.monotonic()
        await page.wait_for_timeout(200)
    api_parents = api_parents or {}
    seen = set(ids)
    for record in raw:
        record.pop('ready');record.pop('snapshot')
        record['timestamp'] = machine_time(record.get('timestamp'))
        record['card'] = listing.locator(CARD + '[data-wev-comment-id=' + json.dumps(record['comment_id']) + ']')
        record['parent_id'] = record['parent_id'] or api_parents.get(record['comment_id'])
        if not record.get('parent_id'):
            short = record['comment_id'].split('-')[-1]
            if short in api_parents and sum(cid.split('-')[-1] == short for cid in seen) == 1:
                record['parent_id'] = api_parents[short]
        parent = record.get('parent_id')
        if parent and parent not in seen:
            matches = [cid for cid in seen if cid.split('-')[-1] == str(parent)]
            if len(matches) == 1:
                record['parent_id'] = matches[0]
    return order_comments(raw)


async def avatar_source(card, author_selector, image_selector):
    return await card.evaluate('''(el,{author,image}) => {
        const name=el.querySelector(author), img=el.querySelector(image);
        const label=name ? [...name.childNodes].filter(n=>n.nodeType===3).map(n=>n.textContent).join('').trim() || name.textContent.trim() : '';
        let src=img?.currentSrc || img?.getAttribute('src') || '';
        if (src.startsWith('data:') && img?.complete && img.naturalWidth) {
            try {
                const canvas=document.createElement('canvas');canvas.width=canvas.height=16;
                const context=canvas.getContext('2d');context.drawImage(img,0,0,16,16);
                const pixels=context.getImageData(0,0,16,16).data;
                if(!pixels.some((value,i)=>i%4===3 && value))src='';
            } catch {}
        }
        return {author:label,src};
    }''', {'author':author_selector,'image':image_selector})


async def artist_avatar_sources(root, author_selector, records, include_main=True):
    sources = {}
    if include_main:
        main = await avatar_source(root, author_selector, '.avatar-decorator-_-image img, .community-artist-postId-_-header img')
        if main['author'] and main['src']:
            sources[main['author']] = main['src']
    for record in records:
        avatar = await avatar_source(record['card'], AUTHOR, '.comment-item-_-image_area img')
        record['avatar_author'] = avatar['author']
        if avatar['author'] and avatar['src']:
            sources.setdefault(avatar['author'], avatar['src'])
    return sources


async def ensure_comment_avatar(card):
    images = card.locator('.comment-item-_-image_area img')
    if await images.count() != 1 or not await images.is_visible():
        raise ValueError('艺人评论头像不可见，已停止截图，请等待头像加载后重试。')
    valid = await images.evaluate('''img => {
        if(!img.complete || !img.naturalWidth)return false;
        if(!img.src.startsWith('data:'))return true;
        try {
            const canvas=document.createElement('canvas');canvas.width=canvas.height=16;
            const ctx=canvas.getContext('2d');ctx.drawImage(img,0,0,16,16);
            return ctx.getImageData(0,0,16,16).data.some((value,i)=>i%4===3 && value);
        } catch {return false;}
    }''')
    if not valid:
        raise ValueError('艺人评论头像仍是空白占位图，未找到同一艺人的真实头像，已停止截图。请等待网页头像加载后重试。')


async def stage_comment(page, record, width, heading='', avatar_sources=None):
    """Keep original contents/assets/CSS, normalize layout before native capture."""
    handle = await record['card'].evaluate_handle('''(original,{width,depth,reply,heading,avatarSource}) => {
        document.querySelector('[data-wvbot-stage]')?.remove();
        const stage=document.createElement('div');stage.dataset.wvbotStage='';
        stage.style.cssText=`position:relative;box-sizing:border-box;width:${width}px;padding:16px;background:white;color:#111;overflow:hidden;`;
        const body=original.querySelector('.line-clamp-node-view-_-container');
        stage.style.fontFamily=getComputedStyle(body).fontFamily;
        stage.style.fontSize=getComputedStyle(body).fontSize;
        if(heading){const title=document.createElement('div');title.textContent=heading;
            title.style.cssText='font-size:14px;font-weight:600;margin-bottom:16px;line-height:1.6';stage.append(title);}
        const card=original.cloneNode(true);card.removeAttribute('href');
        const indent=Math.min(depth,4)*28;
        card.style.cssText=`position:relative!important;display:flex!important;align-items:flex-start!important;gap:12px!important;box-sizing:border-box!important;margin:0 0 0 ${indent}px!important;padding:0!important;width:calc(100% - ${indent}px)!important;height:auto!important;min-height:0!important;background:white!important;text-decoration:none!important;color:inherit!important;`;
        const avatar=card.querySelector('.comment-item-_-image_area');
        if(avatar){
            const avatarSize=reply?24:32;
            avatar.style.cssText=`position:static!important;display:block!important;flex:0 0 ${avatarSize}px!important;width:${avatarSize}px!important;height:${avatarSize}px!important;visibility:visible!important;opacity:1!important;transform:none!important;`;
            for(const n of avatar.querySelectorAll('*'))n.style.cssText=`position:static!important;display:block!important;box-sizing:border-box!important;width:${avatarSize}px!important;height:${avatarSize}px!important;margin:0!important;padding:0!important;visibility:visible!important;opacity:1!important;transform:none!important;border-radius:50%!important;overflow:hidden!important;`;
            const img=avatar.querySelector('img');
            if(img){
                if(avatarSource){img.removeAttribute('srcset');img.src=avatarSource;}
                else if(original.querySelector('.comment-item-_-image_area img')?.currentSrc){img.removeAttribute('srcset');img.src=original.querySelector('.comment-item-_-image_area img').currentSrc;}
                img.loading='eager';img.decoding='sync';img.style.setProperty('object-fit','cover','important');
            }
        }
        const area=card.querySelector('.comment-item-_-text_area');
        area.style.cssText=`box-sizing:border-box!important;position:relative!important;flex:1!important;min-width:0!important;margin:0!important;width:auto!important;height:auto!important;background:white!important;padding:${reply?'0':'14px 16px'}!important;border:${reply?'0':'1px solid #e5e9f2'}!important;border-radius:${reply?'0':'18px'}!important;`;
        if(!reply)area.dataset.wvbotFrame='';
        for(const n of card.querySelectorAll('.comment-item-_-more_wrap,.comment-item-_-translate'))n.style.setProperty('display','none','important');
        // The viewer may retain a short box while its child lines overflow.
        // Normalize the entire original text chain before measuring insertion.
        for(const n of card.querySelectorAll('.comment-item-content-_-comment,.line-clamp-node-view-_-wrap,.line-clamp-node-view-_-container')){
            for(const [key,value] of Object.entries({position:'static',display:'block',height:'auto','min-height':'0','max-height':'none',overflow:'visible','-webkit-line-clamp':'unset','line-clamp':'unset',transform:'none'}))n.style.setProperty(key,value,'important');
        }
        const interaction=card.querySelector('.comment-item-_-interaction');
        if(interaction){
            interaction.style.cssText='position:static!important;display:block!important;height:auto!important;overflow:visible!important;margin-top:12px!important;transform:none!important;';
            for(const n of interaction.querySelectorAll('.toolbar-_-container,.toolbar-_-left,.toolbar-_-right'))n.style.cssText='position:static!important;display:flex!important;align-items:center!important;gap:16px!important;height:auto!important;margin:0!important;padding:0!important;transform:none!important;';
            const originalColors=[...original.querySelectorAll('.comment-item-_-interaction button')].map(n=>getComputedStyle(n).color);
            [...interaction.querySelectorAll('button')].forEach((n,i)=>{n.style.cssText=`position:static!important;display:inline-flex!important;align-items:center!important;gap:6px!important;line-height:1.4!important;height:auto!important;margin:0!important;padding:0!important;background:transparent!important;border:0!important;color:${originalColors[i] || 'inherit'}!important;transform:none!important;`;});
        }
        const text=card.querySelector('.line-clamp-node-view-_-container');
        text.style.setProperty('white-space','pre-wrap','important');
        text.style.setProperty('overflow-wrap','anywhere','important');
        for(const icon of card.querySelectorAll('.icon-_-icon')) {
            icon.style.setProperty('display','inline-flex','important');
            for(const svg of icon.querySelectorAll('svg'))svg.style.cssText='width:100%!important;height:100%!important;display:block!important;';
        }
        for(const blind of card.querySelectorAll('.blind'))blind.style.setProperty('display','none','important');
        const name=card.querySelector('.comment-item-header-profile-name-_-name');
        name.style.cssText='display:inline-flex!important;align-items:center!important;gap:4px!important;font-weight:600!important;text-decoration:none!important;color:inherit!important;';
        const header=card.querySelector('.comment-item-header-_-container');
        header.style.cssText='display:flex!important;align-items:center!important;flex-wrap:wrap!important;gap:8px!important;margin-bottom:4px!important;';
        const time=card.querySelector('.comment-item-header-_-time');
        time.style.cssText='font-size:12px!important;color:#9a9a9a!important;white-space:nowrap!important;';
        if(reply){const line=document.createElement('span');line.dataset.wvbotConnector='';line.setAttribute('role','img');
            line.style.cssText='position:absolute;left:11px;top:36px;bottom:0;border-left:1px solid #e5e9f2;width:1px;';card.append(line);}
        stage.append(card);document.body.append(stage);return stage;
    }''', {'width': width, 'depth': record['depth'], 'reply': record['is_reply'], 'heading': heading, 'avatarSource': (avatar_sources or {}).get(record.get('avatar_author'))})
    await handle.dispose()
    await page.add_style_tag(content='[data-wvbot-stage] *::before,[data-wvbot-stage] *::after{content:none!important;display:none!important}')
    return page.locator('[data-wvbot-stage]')


async def decoration(card):
    return await card.evaluate('''el => {
        const a=el.getBoundingClientRect(),frame=el.querySelector('[data-wvbot-frame]'),line=el.querySelector('[data-wvbot-connector]');
        const r=frame?.getBoundingClientRect(), l=line?.getBoundingClientRect();
        return {frame:r?{left:r.left-a.left,right:r.right-a.left-1,color:'#e5e9f2'}:null,
                connector_x:l?l.left-a.left:null};
    }''')

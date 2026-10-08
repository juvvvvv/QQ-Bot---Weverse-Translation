"""Render a native comment in its own document, with translation in normal flow."""
import base64
import io
from PIL import Image

HTML = """<!doctype html><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'; font-src https://qqbot-font.invalid">
<style>
html,body{margin:0;padding:0;background:white}
*{box-sizing:border-box}
#card{background:white;color:#111;overflow:hidden}
#heading{font-weight:600;line-height:1.6}
#row{position:relative;display:flex;align-items:flex-start}
#avatar{display:block;object-fit:cover;border-radius:50%;flex-shrink:0}
#area{flex:1;min-width:0;background:white}
#header{display:flex;align-items:center;flex-wrap:wrap}
#name{display:inline-flex;align-items:center;font-weight:600}
#time{color:#9a9a9a;white-space:nowrap}
#original,#translation{white-space:pre-wrap;overflow-wrap:anywhere;position:static;height:auto;max-height:none}
#translation{font-family:QQBotCustom,"Microsoft YaHei","PingFang SC","Noto Sans CJK SC",sans-serif;color:#111}
#toolbar{display:flex;align-items:center;color:#cdd0d4}
.tool{display:inline-flex;align-items:center}
svg{display:block;flex-shrink:0}
#connector{position:absolute;width:0;border-left:1px solid #e5e9f2}
</style><div id="card"><div id="heading"></div><div id="row"><img id="avatar"><div id="area"><div id="header"><span id="name"></span><span id="time"></span></div><div id="original"></div><div id="translation"></div><div id="toolbar"></div></div><span id="connector"></span></div></div>"""


async def embedded_image(page, image):
    src = await image.evaluate('img=>img.currentSrc || img.src')
    if src.startswith('data:'):
        return src
    # Use the browser context's already authorized resource session, never ask
    # the rendering document to revisit Weverse or carry login data.
    response = await page.context.request.get(src, timeout=15000)
    if not response.ok:
        raise ValueError('评论图片读取失败，请等待图片加载后重新读取。')
    data = await response.body()
    if len(data) > 8_000_000:
        raise ValueError('评论图片过大。')
    with Image.open(io.BytesIO(data)) as image:
        image.thumbnail((1024, 1024))
        stream = io.BytesIO();image.convert('RGBA').save(stream, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(stream.getvalue()).decode()


async def comment_model(page, stage, record, heading=''):
    # Serialize a small allowlisted tree rather than arbitrary webpage HTML,
    # event handlers, CSS positioning, duplicated hidden content or overlays.
    model = await stage.evaluate(r'''el => {
        const body=el.querySelector('.line-clamp-node-view-_-container'),style=getComputedStyle(body);
        const tree=n=>{
            if(n.nodeType===3)return {text:n.data};
            if(n.nodeType!==1 || n.matches('script,style,svg,.blind,[aria-hidden="true"]'))return null;
            const s=getComputedStyle(n);
            if(s.display==='none' || s.visibility==='hidden')return null;
            const tag=['br','strong','b','em','i','u','a','p','img'].includes(n.localName)?n.localName:'span';
            return {tag,children:[...n.childNodes].map(tree).filter(Boolean),
                    color:s.color,weight:s.fontWeight,font_style:s.fontStyle,
                    decoration:s.textDecorationLine,block:s.display==='block',
                    emoji:n.hasAttribute('data-wvbot-emoji'),src:tag==='img'?n.currentSrc:null};
        };
        const svg=n=>n?{color:getComputedStyle(n).color,attrs:Object.fromEntries([...n.attributes].filter(a=>['viewBox','fill','width','height'].includes(a.name)).map(a=>[a.name,a.value])),
            children:[...n.children].filter(c=>['g','path','rect','circle','ellipse','line','polyline','polygon'].includes(c.localName)).map(c=>({tag:c.localName,
                attrs:Object.fromEntries([...c.attributes].filter(a=>['d','fill','fill-rule','clip-rule','stroke','stroke-width','stroke-linecap','stroke-linejoin','x','y','x1','x2','y1','y2','cx','cy','r','rx','ry','width','height','points','transform'].includes(a.name)).map(a=>[a.name,a.value])),
                children:c.localName==='g'?svg(c).children:[]}))}:null;
        const name=el.querySelector('.comment-item-header-profile-name-_-name');
        return {body:[...body.childNodes].map(tree).filter(Boolean),font:style.fontFamily,
                color:style.color,font_size:parseFloat(style.fontSize),line_height:parseFloat(style.lineHeight)||parseFloat(style.fontSize)*1.6,
                name:[...name.childNodes].filter(n=>n.nodeType===3).map(n=>n.textContent).join('').trim(),
                badge:svg(name.querySelector('svg')),
                time:el.querySelector('.comment-item-header-_-time').innerText,
                tools:[...el.querySelectorAll('.comment-item-_-interaction button')].map(button=>({
                    icon:svg(button.querySelector('svg')),
                    text:[...button.querySelectorAll('.blind')].reduce((text,n)=>text.replace(n.textContent,''),button.textContent).trim(),
                    color:getComputedStyle(button).color}))};
    }''')
    model.update(avatar=await embedded_image(page,stage.locator('.comment-item-_-image_area img')),
                 reply=record['is_reply'],depth=record['depth'],heading=heading)
    # Inline original images, if any, are embedded as well as avatars.
    images=stage.locator('.line-clamp-node-view-_-container img')
    sources={await images.nth(i).evaluate('img=>img.currentSrc'):await embedded_image(page,images.nth(i)) for i in range(await images.count())}
    def embed(nodes):
        for node in nodes:
            if node.get('src'):node['src']=sources[node['src']]
            embed(node.get('children',[]))
    embed(model['body'])
    return model


async def render_comment(page, width, model, scale=1, translation='', font_path=''):
    await page.set_content(HTML)
    if font_path:
        from .text_image import FONT_URL
        loaded=await page.evaluate('''async url=>{try{const font=await new FontFace('QQBotCustom',`url("${url}")`).load();document.fonts.add(font);return true}catch{return false}}''',FONT_URL)
        if not loaded:raise ValueError('浏览器无法加载自定义字体，请检查字体路径。')
    await page.evaluate(r'''({width,m,s,translation})=>{
        const $=id=>document.getElementById(id),px=n=>n*s+'px';
        const svg=(model,size)=>{
            if(!model)return document.createTextNode('');
            const build=(tag,node)=>{const e=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const [k,v] of Object.entries(node.attrs||{}))e.setAttribute(k,v);for(const c of node.children||[])e.append(build(c.tag,c));return e;};
            const icon=build('svg',model);icon.style.width=icon.style.height=px(size);icon.style.color=model.color;return icon;
        };
        $('card').style.cssText=`width:${width}px;padding:${px(16)};font-family:${m.font};font-size:${px(m.font_size)}`;
        $('heading').textContent=m.heading;
        $('heading').style.cssText=`display:${m.heading?'block':'none'};font-size:${px(14)};margin-bottom:${px(16)}`;
        const indent=Math.min(m.depth,4)*28;
        $('row').style.cssText=`margin-left:${px(indent)};gap:${px(12)}`;
        const av=m.reply?24:32;$('avatar').src=m.avatar;$('avatar').style.width=$('avatar').style.height=px(av);
        $('area').style.cssText=m.reply?'':`padding:${px(14)} ${px(16)};border:${px(1)} solid #e5e9f2;border-radius:${px(18)}`;
        $('header').style.cssText=`gap:${px(8)};margin-bottom:${px(4)}`;
        $('name').textContent=m.name;$('name').style.gap=px(4);$('name').append(svg(m.badge,12));
        $('time').textContent=m.time;$('time').style.fontSize=px(12);
        const tree=node=>{
            if('text' in node)return document.createTextNode(node.text);
            const e=document.createElement(node.tag);
            e.style.color=node.color;e.style.fontWeight=node.weight;e.style.fontStyle=node.font_style;e.style.textDecoration=node.decoration;
            if(node.block)e.style.display='block';
            if(node.emoji)e.style.fontFamily='"Apple Color Emoji","Segoe UI Emoji","Noto Color Emoji",sans-serif';
            if(node.src)e.src=node.src;
            for(const child of node.children||[])e.append(tree(child));return e;
        };
        $('original').replaceChildren(...m.body.map(tree));$('original').style.lineHeight=px(m.line_height);$('original').style.color=m.color;
        const chinese=translation && translation!=='/k';
        $('translation').textContent=chinese?translation:'';
        $('translation').style.cssText=`display:${chinese?'block':'none'};font-size:${px(Math.max(12,Math.min(48,m.font_size)))};line-height:1.65;margin-top:${px(6)}`;
        $('toolbar').style.cssText=`gap:${px(16)};margin-top:${px(12)};line-height:1.4`;
        for(const tool of m.tools){const row=document.createElement('span');row.className='tool';row.style.gap=px(6);row.style.color=tool.color;row.append(svg(tool.icon,20),document.createTextNode(tool.text));$('toolbar').append(row);}
        $('connector').style.cssText=`display:${m.reply?'block':'none'};left:${px(11)};top:${px(36)};bottom:0;border-left-width:${px(1)}`;
    }''',{'width':width,'m':model,'s':scale,'translation':translation})
    from .page_cleanup import prepare_emoji_text
    await prepare_emoji_text(page.locator('#translation'))
    await page.evaluate('''async()=>{await document.fonts.ready;for(const img of document.images){if(!img.complete)await Promise.race([new Promise(r=>{img.onload=img.onerror=r}),new Promise(r=>setTimeout(r,10000))]);if(!img.naturalWidth)throw new Error('image incomplete')}}''')
    height=await page.locator('#card').evaluate('el=>el.getBoundingClientRect().height')
    if height>30000 or width*height>24_000_000:raise ValueError('评论图片过大，请分批处理。')
    return await page.locator('#card').screenshot(type='png',animations='disabled')

"""Capture the original post's action bar independently of site clipping CSS."""
import io
import math
from PIL import Image
from .capture_layout import measure_card, screenshot_card

HTML='''<!doctype html><meta charset="utf-8"><style>
*{box-sizing:border-box}body{margin:0;background:white}
#post-footer{background:white;overflow:visible}
#tools{display:flex;align-items:center;flex-wrap:wrap;gap:16px}
#left,#right{display:flex;align-items:center;flex-wrap:wrap;gap:16px}
.tool{display:inline-flex;align-items:center;flex-shrink:0;gap:6px;white-space:nowrap}
svg{display:block;flex:none;width:20px;height:20px}
</style><div id="post-footer"><div id="tools"><div id="left"></div><div id="right"></div></div></div>'''


async def toolbar_model(toolbar):
    return await toolbar.evaluate(r'''el=>{
        const attributes=['viewBox','d','fill','fill-rule','clip-rule','stroke','stroke-width','stroke-linecap','stroke-linejoin','x','y','x1','x2','y1','y2','cx','cy','r','rx','ry','width','height','points','transform','opacity','fill-opacity','stroke-opacity'];
        const tags=['svg','g','path','rect','circle','ellipse','line','polyline','polygon'];
        const svg=n=>{
            if(!n || !tags.includes(n.localName))return null;
            const attrs=Object.fromEntries([...n.attributes].filter(a=>attributes.includes(a.name) && !/url\s*\(|https?:|javascript:/i.test(a.value)).map(a=>[a.name,a.value]));
            const s=getComputedStyle(n);
            // Freeze paints resolved by the website's CSS. No handlers, URLs,
            // positioning rules or site clipping styles enter the renderer.
            for(const key of ['fill','stroke']){
                const value=s.getPropertyValue(key);
                if(value && !value.includes('url('))attrs[key]=value;
            }
            return {tag:n.localName,attrs,children:[...n.children].map(svg).filter(Boolean)};
        };
        const container=el.querySelector('.toolbar-_-container'),left=el.querySelector('.toolbar-_-left'),right=el.querySelector('.toolbar-_-right');
        const gap=parseFloat(container?getComputedStyle(container).gap:'')||16;
        return {gap,spaced_right:!!(left && right && right.getBoundingClientRect().left-left.getBoundingClientRect().right>gap+1),
                tools:[...el.querySelectorAll('button')].map(button=>{
            const clone=button.cloneNode(true);clone.querySelectorAll('.blind,svg,[aria-hidden="true"]').forEach(n=>n.remove());
            const s=getComputedStyle(button);
            return {text:clone.textContent.trim(),icon:svg(button.querySelector('svg')),
                color:s.color,font:s.fontFamily,size:parseFloat(s.fontSize)||14,weight:s.fontWeight,
                right:!!button.closest('.toolbar-_-right')};
        })};
    }''')


async def render_toolbar(page,width,model,padding,left,right):
    await page.set_content(HTML)
    await page.evaluate(r'''({width,m,padding,left,right})=>{
        const footer=document.getElementById('post-footer'),tools=document.getElementById('tools');
        footer.style.cssText=`width:${width}px;padding:0 ${right}px ${padding}px ${left}px`;
        tools.style.gap=m.gap+'px';
        if(m.spaced_right)document.getElementById('right').style.marginLeft='auto';
        const build=node=>{
            const el=document.createElementNS('http://www.w3.org/2000/svg',node.tag);
            for(const [name,value] of Object.entries(node.attrs))el.setAttribute(name,value);
            for(const child of node.children)el.append(build(child));return el;
        };
        for(const tool of m.tools){
            const row=document.createElement('span');row.className='tool';
            const size=Math.max(10,Math.min(48,tool.size));
            row.style.cssText=`color:${tool.color};font-family:${tool.font};font-size:${size}px;font-weight:${tool.weight};line-height:${Math.max(20,size*1.4)}px`;
            if(tool.icon)row.append(build(tool.icon));
            const text=document.createElement('span');text.textContent=tool.text;row.append(text);
            document.getElementById(tool.right?'right':'left').append(row);
        }
    }''',{'width':width,'m':model,'padding':padding,'left':left,'right':right})
    await page.evaluate('document.fonts.ready')
    layout=await page.locator('#post-footer').evaluate('''el=>{
        const root=el.getBoundingClientRect();
        const rect=n=>{const r=n.getBoundingClientRect();return {x:r.x-root.x,y:r.y-root.y,width:r.width,height:r.height}};
        return {height:root.height,icons:[...el.querySelectorAll('svg')].map(rect),
                texts:[...el.querySelectorAll('.tool>span')].map(rect)};
    }''')
    if layout['height']>30000 or width*layout['height']*await page.evaluate('devicePixelRatio**2')>24_000_000:
        raise ValueError('动态互动栏图片过大，请降低截图倍率。')
    return await page.locator('#post-footer').screenshot(type='png',animations='disabled'),layout


async def capture_post_card(page,card,text_selector):
    toolbar=card.locator('[data-wvbot-owned-toolbar]')
    if await toolbar.count()!=1:
        bounds=await measure_card(card,text_selector)
        return await screenshot_card(page,bounds,card),bounds,None
    model=await toolbar_model(toolbar)
    if not model['tools']:
        bounds=await measure_card(card,text_selector)
        return await screenshot_card(page,bounds,card),bounds,None
    geometry=await toolbar.evaluate('''el=>{
        const card=el.parentElement,r=card.getBoundingClientRect(),s=getComputedStyle(card);
        const buttons=[...el.querySelectorAll('button')],first=buttons[0]?.getBoundingClientRect();
        const container=el.querySelector('.toolbar-_-container');
        return {top:Math.min(...buttons.map(n=>n.getBoundingClientRect().top-r.top)),
                left:first?Math.max(0,first.left-r.left):parseFloat(s.paddingLeft)||0,
                right:(parseFloat(s.paddingRight)||0)+(container?parseFloat(getComputedStyle(container).paddingRight)||0:0)};
    }''')
    style=await toolbar.get_attribute('style')
    try:
        # Capture only the original content. Neither a partially visible bar
        # nor a clipped duplicate can become part of the body PNG.
        # Keep its geometry: shrinking a masked/clipped card would move the
        # website's bottom clipping onto the last photograph instead.
        await toolbar.evaluate("el=>el.style.setProperty('visibility','hidden','important')")
        bounds=await measure_card(card,text_selector)
        bounds['target_height']=max(geometry['top'],bounds['content_bottom']+bounds['padding'])
        raw=await screenshot_card(page,bounds,card)
    finally:
        await toolbar.evaluate('(el,style)=>{if(style===null)el.removeAttribute("style");else el.setAttribute("style",style)}',style)
    body=Image.open(io.BytesIO(raw)).convert('RGB')
    scale=body.width/bounds['w']
    body_height=math.ceil(bounds['target_height']*scale)
    if body.height<body_height:
        padded=Image.new('RGB',(body.width,body_height),'white');padded.paste(body);body=padded
    isolated=await page.context.new_page()
    try:
        await isolated.set_viewport_size({'width':max(240,math.ceil(bounds['w'])),'height':1000})
        footer_raw,layout=await render_toolbar(isolated,bounds['w'],model,max(16,bounds['padding']),geometry['left'],geometry['right'])
    finally:await isolated.close()
    footer=Image.open(io.BytesIO(footer_raw)).convert('RGB')
    if footer.width!=body.width:
        # Fractional CSS widths can round differently in element/page clips.
        # Keep the source width and pixel density consistent across fragments.
        footer=footer.resize((body.width,round(footer.height*body.width/footer.width)),Image.Resampling.LANCZOS)
    height=body.height+footer.height
    if height>30000 or height*body.width>24_000_000:
        raise ValueError('动态图片过大，请降低截图倍率。')
    result=Image.new('RGB',(body.width,height),'white')
    result.paste(body);result.paste(footer,(0,body.height))
    layout={**layout,'top':body.height,'height':footer.height,
            'icons':[{k:round(v*scale) for k,v in rect.items()} for rect in layout['icons']],
            'texts':[{k:round(v*scale) for k,v in rect.items()} for rect in layout['texts']],
            'texts_display':[tool['text'] for tool in model['tools']]}
    bounds['target_height']=height/scale
    bounds['trailing_text']=False
    bounds['padding']=max(16,bounds['padding'])
    out=io.BytesIO();result.save(out,format='PNG')
    return out.getvalue(),bounds,layout

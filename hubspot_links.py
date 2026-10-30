"""Source-only HubSpot contact links. Never accept a model-generated CRM URL."""
import html
import re
from urllib.parse import urlsplit

PATH = re.compile(r'/contacts/([0-9]+)/(?:contact/|record/0-1/)([0-9]+)/?')
HOST = re.compile(r'app(?:-[a-z]{2}[0-9]+)?\.hubspot\.com')


def canonical(value):
    if not isinstance(value,str) or re.search(r'[\s<>]',value):return None
    try:
        parsed=urlsplit(html.unescape(value))
        if parsed.scheme!='https' or not HOST.fullmatch(parsed.netloc):return None
        match=PATH.fullmatch(parsed.path)
        if not match:return None
        return 'https://'+parsed.netloc+'/contacts/'+match[1]+'/record/0-1/'+match[2]
    except ValueError:return None


def extract(message):
    """Support Block Kit, rich-text links and legacy attachment actions; reject ambiguity."""
    buttons=set();links=set()
    def walk(value):
        if isinstance(value,list):
            for v in value:walk(v)
        elif isinstance(value,dict):
            url=canonical(value.get('url'))
            if url:
                (buttons if value.get('type')=='button' else links).add(url)
            for key in ('text','fallback','pretext'):
                text=value.get(key)
                if isinstance(text,str):
                    for candidate in re.findall(r'https://[^\s<>|]+',html.unescape(text)):
                        found=canonical(candidate.rstrip('.,;)'))
                        if found:links.add(found)
                elif isinstance(text,dict):walk(text)
            for key in ('blocks','elements','fields','attachments','actions','accessory'):
                if key in value:walk(value[key])
    walk(message)
    candidates=buttons or links
    return next(iter(candidates)) if len(candidates)==1 else None


def source_url(event):
    if 'hubspot_url' in event:return canonical(event['hubspot_url'])
    return extract({'text':event.get('text','')})


def hydrate(data,clients):
    """Older saved reviews can recover the button from their verified HubSpot parent."""
    source=data.get('source_event') or {}
    if 'hubspot_url' not in source and source.get('kind') in ('review','review_manual'):
        try:clients.parent(source)  # Verifies parent identity and sets source metadata.
        except Exception:pass  # Missing source is disclosed, never guessed.
    data['hubspot_url']=source_url(source)
    return data

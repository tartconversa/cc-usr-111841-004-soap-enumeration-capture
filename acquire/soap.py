"""SOAP 1.2 枚举消息构造与解析；只认 namespace，不认前缀。"""
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from xml.sax.saxutils import escape

SOAP_NS='http://www.w3.org/2003/05/soap-envelope'
ENUM_NS='urn:hall:enumeration'

class SourceFault(Exception):
    """来源返回的 Fault；code 为 Subcode Value 的本地名（如 ContextExpired/ScopeDenied）。"""
    def __init__(self, code, reason=''):
        self.code=code
        self.reason=reason
        super().__init__(f'{code}: {reason}' if reason else code)

@dataclass
class PullPage:
    generation: object
    rows: list
    context: object
    end: bool
    response: object=None

def _envelope(inner):
    return (f'<s:Envelope xmlns:s="{SOAP_NS}" xmlns:e="{ENUM_NS}">'
            f'<s:Body>{inner}</s:Body></s:Envelope>').encode('utf-8')

def enumerate_body(scope):
    return _envelope(f'<e:Enumerate><e:Scope>{escape(scope)}</e:Scope></e:Enumerate>')

def pull_body(context, max_elements):
    return _envelope(f'<e:Pull><e:EnumerationContext>{escape(context)}</e:EnumerationContext>'
                     f'<e:MaxElements>{max_elements}</e:MaxElements></e:Pull>')

def release_body(context):
    return _envelope(f'<e:Release><e:EnumerationContext>{escape(context)}</e:EnumerationContext></e:Release>')

def parse_envelope(body):
    try:
        root=ET.fromstring(body)
    except ET.ParseError as exc:
        raise ValueError(f'not a SOAP envelope: {exc}') from exc
    if root.tag!=f'{{{SOAP_NS}}}Envelope':
        raise ValueError('not a SOAP 1.2 envelope')
    return root

def _localname(text):
    return text.rsplit('}',1)[-1].rsplit(':',1)[-1].strip()

def parse_fault(root):
    """Envelope 中的 Fault 转 SourceFault；无 Fault 返回 None。"""
    fault=root.find(f'.//{{{SOAP_NS}}}Body/{{{SOAP_NS}}}Fault')
    if fault is None:
        return None
    code=''
    subcode=fault.find(f'{{{SOAP_NS}}}Code/{{{SOAP_NS}}}Subcode/{{{SOAP_NS}}}Value')
    if subcode is not None and subcode.text:
        code=_localname(subcode.text)
    if not code:
        value=fault.find(f'{{{SOAP_NS}}}Code/{{{SOAP_NS}}}Value')
        if value is not None and value.text:
            code=_localname(value.text)
    reason=''
    text=fault.find(f'{{{SOAP_NS}}}Reason/{{{SOAP_NS}}}Text')
    if text is not None and text.text:
        reason=text.text.strip()
    return SourceFault(code or 'Fault',reason)

def parse_items(root):
    """Items 下的 Window/Capability 提取为 key/value 行；id 是唯一身份，显示名不参与。"""
    rows=[]
    for item in root.findall(f'.//{{{ENUM_NS}}}Items/*'):
        if 'id' not in item.attrib:
            raise ValueError('item without id')
        rows.append({'key':item.attrib['id'],
                     'value':{'type':item.tag.rsplit('}',1)[-1],**item.attrib}})
    return rows

def parse_enumerate_response(root):
    node=root.find(f'.//{{{SOAP_NS}}}Body/{{{ENUM_NS}}}EnumerateResponse')
    if node is None:
        raise ValueError('expected EnumerateResponse')
    context=node.find(f'{{{ENUM_NS}}}EnumerationContext')
    text=(context.text or '').strip() if context is not None else ''
    if not text:
        raise ValueError('EnumerateResponse without EnumerationContext')
    return text

def parse_pull_response(root):
    """PullResponse：generation、Items、新上下文；只有 EndOfSequence 才表示结束。"""
    node=root.find(f'.//{{{SOAP_NS}}}Body/{{{ENUM_NS}}}PullResponse')
    if node is None:
        raise ValueError('expected PullResponse')
    context=node.find(f'{{{ENUM_NS}}}EnumerationContext')
    text=(context.text or '').strip() if context is not None else ''
    return PullPage(generation=node.attrib.get('generation'),
                    rows=parse_items(node),
                    context=text or None,
                    end=node.find(f'{{{ENUM_NS}}}EndOfSequence') is not None)

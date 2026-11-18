"""完整领取 SOAP 枚举：Enumerate/Pull/Release、失效重领、范围撤回与配置视图。"""
import argparse
import hashlib
import json
from .soap import (SourceFault, enumerate_body, pull_body, release_body,
                   parse_envelope, parse_fault, parse_enumerate_response, parse_pull_response)
from .store import Store
from .transport import fetch

class EnumerationClient:
    """单个来源入口的 SOAP 枚举客户端；每次调用都是真实 HTTP POST，不做本地扮演。"""
    def __init__(self, url, *, headers=None, max_bytes=1048576, timeout=5):
        self.url=url
        self.headers={'Content-Type':'application/soap+xml', **(headers or {})}
        self.max_bytes=max_bytes
        self.timeout=timeout

    def _post(self, body):
        response=fetch(self.url, method='POST', headers=self.headers, body=body,
                       max_bytes=self.max_bytes, timeout=self.timeout)
        try:
            root=parse_envelope(response.body)
        except ValueError:
            if not 200<=response.status<300:
                raise ValueError(f'source status {response.status}')
            raise
        fault=parse_fault(root)
        if fault is not None:
            raise fault
        if not 200<=response.status<300:
            raise ValueError(f'source status {response.status}')
        return root, response

    def enumerate(self, scope):
        root,_=self._post(enumerate_body(scope))
        return parse_enumerate_response(root)

    def pull(self, context, max_elements):
        root,response=self._post(pull_body(context,max_elements))
        page=parse_pull_response(root)
        page.response=response
        return page

    def release(self, context):
        try:
            self._post(release_body(context))
            return True
        except SourceFault:
            return False

def _receipt(url, body):
    return hashlib.sha256(url.encode()+b'\0'+body).hexdigest()

def _mark_fault(store, identifier, fault):
    state={'ContextExpired':'expired','ScopeDenied':'withdrawn'}.get(fault.code)
    detail=f'fault {fault.code}: {fault.reason}' if fault.reason else f'fault {fault.code}'
    if state is None:
        store.set_enumeration(identifier, detail=detail)
    else:
        store.set_enumeration(identifier, state=state, context=None, detail=detail)

def _release_one(store, client, record):
    note='released'
    if record['context']:
        note='released' if client.release(record['context']) else 'release rejected by source'
    store.set_enumeration(record['id'], state='released', context=None, detail=note)

def collect(url, database, scope, *, max_elements=100, max_pages=100, max_reclaims=2,
            restart=False, headers=None, client=None):
    """领取 (url, scope) 的完整窗口配置，返回 Store.configuration 视图。

    默认继续未失效的已存上下文；restart=True 先释放旧上下文再明确重开。
    上下文会旋转：每次 Pull 成功后按响应中的新 EnumerationContext 继续；
    响应可以没有 Items，但只有 EndOfSequence 才表示结束。
    ContextExpired 自动重领（最多 max_reclaims 次，新枚举可能是新 generation）；
    ScopeDenied 撤回本次配置，不拿旧权限下的资料冒充当前完整结果。
    max_pages 是单次调用的页预算；预算用尽时上下文留在库中，下次调用继续。
    """
    if max_elements<1:
        raise ValueError('max_elements must be positive')
    if max_pages<1:
        raise ValueError('max_pages must be positive')
    client=client or EnumerationClient(url, headers=headers)
    store=Store(database)
    try:
        record=None
        if restart:
            for stale in store.open_enumerations(url, scope):
                _release_one(store, client, stale)
        else:
            opened=store.open_enumerations(url, scope)
            if opened:
                record=opened[0]
                for stale in opened[1:]:
                    _release_one(store, client, stale)
                if not record['context']:
                    store.set_enumeration(record['id'], state='released',
                                          detail='no stored context to resume')
                    record=None
        reclaims=0
        while True:
            if record is None:
                identifier=store.begin_enumeration(url, scope)
                try:
                    context=client.enumerate(scope)
                except SourceFault as fault:
                    _mark_fault(store, identifier, fault)
                    if fault.code=='ContextExpired':
                        reclaims+=1
                        if reclaims<=max_reclaims:
                            continue
                        return store.configuration(url, scope)
                    if fault.code=='ScopeDenied':
                        return store.configuration(url, scope)
                    raise
                store.set_enumeration(identifier, context=context)
                record=dict(id=identifier, context=context)
            pages=0
            while pages<max_pages:
                try:
                    page=client.pull(record['context'], max_elements)
                except SourceFault as fault:
                    _mark_fault(store, record['id'], fault)
                    if fault.code=='ContextExpired':
                        reclaims+=1
                        record=None
                        break
                    if fault.code=='ScopeDenied':
                        return store.configuration(url, scope)
                    raise
                receipt=_receipt(url, page.response.body)
                if not page.end and not page.context:
                    store.save_receipt(receipt, page.response)
                    store.set_enumeration(record['id'],
                        detail='pull response without context or EndOfSequence')
                    raise ValueError('pull response without context or EndOfSequence')
                store.save_pull(record['id'], receipt, page.response, page.rows,
                                generation=page.generation, context=page.context, end=page.end)
                pages+=1
                if page.end:
                    return store.configuration(url, scope)
                record['context']=page.context
            if record is not None:
                return store.configuration(url, scope)
            if reclaims>max_reclaims:
                return store.configuration(url, scope)
    finally:
        store.close()

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('url')
    parser.add_argument('database')
    parser.add_argument('scope')
    parser.add_argument('--max-elements',type=int,default=100)
    parser.add_argument('--max-pages',type=int,default=100)
    parser.add_argument('--restart',action='store_true')
    args=parser.parse_args()
    view=collect(args.url,args.database,args.scope,
                 max_elements=args.max_elements,max_pages=args.max_pages,restart=args.restart)
    print(json.dumps(view,ensure_ascii=False))

if __name__=='__main__':
    main()

import base64
import hashlib
from pathlib import Path
import tempfile
import threading
import unittest
from acquire.enumeration import collect
from acquire.origin import make_server
from acquire.store import GenerationChanged, Store

SOAP='http://www.w3.org/2003/05/soap-envelope'
NS='urn:hall:enumeration'

def envelope(inner):
    return f'<s:Envelope xmlns:s="{SOAP}" xmlns:e="{NS}"><s:Body>{inner}</s:Body></s:Envelope>'

def enumerate_response(context):
    return envelope(f'<e:EnumerateResponse><e:EnumerationContext>{context}</e:EnumerationContext></e:EnumerateResponse>')

def pull_response(generation, items='', context=None, end=False):
    parts=f'<e:PullResponse generation="{generation}">'
    if items:
        parts+=f'<e:Items>{items}</e:Items>'
    if context:
        parts+=f'<e:EnumerationContext>{context}</e:EnumerationContext>'
    if end:
        parts+='<e:EndOfSequence/>'
    return envelope(parts+'</e:PullResponse>')

def fault(code, reason=''):
    return envelope(f'<s:Fault><s:Code><s:Value>s:Receiver</s:Value><s:Subcode><s:Value>e:{code}</s:Value>'
                    f'</s:Subcode></s:Code><s:Reason><s:Text>{reason}</s:Text></s:Reason></s:Fault>')

def route(match, body, status=200):
    return dict(path='/enumeration', method='POST', body_contains=match, status=status,
                headers={'Content-Type':'application/soap+xml'}, body=body)

def sequence_route(match, bodies):
    return dict(path='/enumeration', method='POST', body_contains=match,
                responses=[dict(status=b[1], headers={'Content-Type':'application/soap+xml'}, body=b[0])
                           for b in bodies])

def pull_match(context):
    return f'<e:EnumerationContext>{context}</e:EnumerationContext><e:MaxElements>'

PAGE1='<e:Window id="w1" hall="east"/><e:Capability id="c1" window="w1" service="company-change"/>'
PAGE2='<e:Window id="w2" hall="west"/><e:Capability id="c2" window="w2" service="permit-renewal"/>'

def happy_routes():
    return [
        route('<e:Enumerate>', enumerate_response('ctx-1')),
        route(pull_match('ctx-1'), pull_response('g1', PAGE1, context='ctx-2')),
        route(pull_match('ctx-2'), pull_response('g1', PAGE2, end=True)),
        route('<e:Release>', envelope('<e:ReleaseResponse/>')),
    ]

class EnumerationTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.database=str(Path(self.temporary.name)/'data.sqlite3')
        self.scenario={'entry_path':'/enumeration','media':'application/soap+xml','routes':happy_routes()}
        self.server=make_server(self.scenario)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.url=f'http://127.0.0.1:{self.server.server_port}/enumeration'
    def tearDown(self):
        self.server.shutdown(); self.thread.join(); self.server.server_close()
        self.temporary.cleanup()
    def requests(self):
        return [base64.b64decode(e['body_base64']).decode() for e in self.server.events]
    def view(self):
        store=Store(self.database)
        try:
            return store.configuration(self.url,'hall-east')
        finally:
            store.close()

    def test_full_enumeration_publishes_with_provenance(self):
        result=collect(self.url,self.database,'hall-east')
        self.assertEqual(result['state'],'published')
        self.assertEqual(result['generation'],'g1')
        self.assertEqual([e['key'] for e in result['entities']],['c1','c2','w1','w2'])
        self.assertEqual(result['missing'],[])
        requests=self.requests()
        self.assertEqual(len(requests),3)
        self.assertIn('<e:Enumerate>',requests[0])
        self.assertIn('<e:EnumerationContext>ctx-1</e:EnumerationContext>',requests[1])
        self.assertIn('<e:EnumerationContext>ctx-2</e:EnumerationContext>',requests[2])
        self.assertFalse(any('<e:Release>' in r for r in requests))
        store=Store(self.database)
        try:
            for entity in result['entities']:
                receipt=store.receipt(entity['receipt'])
                self.assertEqual(receipt['url'],self.url)
                self.assertIn(entity['key'].encode(),receipt['body'])
        finally:
            store.close()

    def test_cross_page_reference_resolves(self):
        self.scenario['routes']=[
            route('<e:Enumerate>', enumerate_response('ctx-1')),
            route(pull_match('ctx-1'), pull_response('g1','<e:Capability id="c1" window="w9" service="permit"/>', context='ctx-2')),
            route(pull_match('ctx-2'), pull_response('g1','<e:Window id="w9" hall="north"/>', end=True)),
        ]
        result=collect(self.url,self.database,'hall-east')
        self.assertEqual(result['state'],'published')
        self.assertEqual(result['missing'],[])

    def test_missing_reference_reported(self):
        self.scenario['routes']=[
            route('<e:Enumerate>', enumerate_response('ctx-1')),
            route(pull_match('ctx-1'), pull_response('g1','<e:Capability id="c1" window="w9" service="permit"/>', end=True)),
        ]
        result=collect(self.url,self.database,'hall-east')
        self.assertEqual(result['state'],'missing-references')
        self.assertEqual(result['missing'],[dict(capability='c1',window='w9')])

    def test_empty_page_without_end_of_sequence_continues(self):
        self.scenario['routes']=[
            route('<e:Enumerate>', enumerate_response('ctx-1')),
            route(pull_match('ctx-1'), pull_response('g1','', context='ctx-2')),
            route(pull_match('ctx-2'), pull_response('g1', PAGE1, end=True)),
        ]
        result=collect(self.url,self.database,'hall-east')
        self.assertEqual(result['state'],'published')
        self.assertEqual(len([r for r in self.requests() if '<e:Pull>' in r]),2)

    def test_context_expired_reclaim_keeps_generations_separate(self):
        self.scenario['routes']=[
            sequence_route('<e:Enumerate>', [(enumerate_response('ctx-1'),200),(enumerate_response('ctx-9'),200)]),
            route(pull_match('ctx-1'), pull_response('g1', PAGE1, context='ctx-2')),
            route(pull_match('ctx-2'), fault('ContextExpired','context rotated away'), status=500),
            route(pull_match('ctx-9'), pull_response('g2','<e:Window id="w7" hall="east"/><e:Capability id="c7" window="w7" service="company-change"/>', end=True)),
        ]
        result=collect(self.url,self.database,'hall-east')
        self.assertEqual(result['state'],'published')
        self.assertEqual(result['generation'],'g2')
        self.assertEqual([e['key'] for e in result['entities']],['c7','w7'])
        self.assertEqual(len([r for r in self.requests() if '<e:Enumerate>' in r]),2)
        store=Store(self.database)
        try:
            records=store.enumerations(self.url,'hall-east')
            self.assertEqual([r['state'] for r in records],['expired','complete'])
            old=store.entities(records[0]['id'])
            self.assertEqual({e['key'] for e in old},{'w1','c1'})
            for entity in old:
                self.assertIsNotNone(store.receipt(entity['receipt']))
        finally:
            store.close()

    def test_scope_denied_withdraws_and_does_not_reuse_old_data(self):
        first=collect(self.url,self.database,'hall-east')
        self.assertEqual(first['state'],'published')
        self.scenario['routes']=[
            route('<e:Enumerate>', fault('ScopeDenied','scope revoked'), status=500),
        ]
        result=collect(self.url,self.database,'hall-east',restart=True)
        self.assertEqual(result['state'],'withdrawn')
        self.assertEqual(result['entities'],[])
        self.assertNotEqual(result['enumeration'],first['enumeration'])
        store=Store(self.database)
        try:
            retained=store.entities(first['enumeration'])
            self.assertEqual({e['key'] for e in retained},{'w1','c1','w2','c2'})
        finally:
            store.close()

    def test_scope_denied_mid_pull_withdraws_partial(self):
        self.scenario['routes']=[
            route('<e:Enumerate>', enumerate_response('ctx-1')),
            route(pull_match('ctx-1'), pull_response('g1', PAGE1, context='ctx-2')),
            route(pull_match('ctx-2'), fault('ScopeDenied','scope narrowed'), status=500),
        ]
        result=collect(self.url,self.database,'hall-east')
        self.assertEqual(result['state'],'withdrawn')
        self.assertEqual(result['lifecycle'],'withdrawn')
        self.assertEqual({e['key'] for e in result['entities']},{'w1','c1'})
        self.assertIn('ScopeDenied',result['note'])

    def test_resume_continues_unexpired_context(self):
        first=collect(self.url,self.database,'hall-east',max_pages=1)
        self.assertEqual(first['state'],'reclaiming')
        self.assertEqual(first['lifecycle'],'open')
        self.assertTrue(first['resumable'])
        self.assertEqual({e['key'] for e in first['entities']},{'w1','c1'})
        second=collect(self.url,self.database,'hall-east')
        self.assertEqual(second['state'],'published')
        self.assertEqual(second['enumeration'],first['enumeration'])
        self.assertEqual(len([r for r in self.requests() if '<e:Enumerate>' in r]),1)

    def test_restart_releases_stored_context(self):
        collect(self.url,self.database,'hall-east',max_pages=1)
        result=collect(self.url,self.database,'hall-east',restart=True)
        self.assertEqual(result['state'],'published')
        requests=self.requests()
        releases=[r for r in requests if '<e:Release>' in r]
        self.assertEqual(len(releases),1)
        self.assertIn('<e:EnumerationContext>ctx-2</e:EnumerationContext>',releases[0])
        self.assertEqual(len([r for r in requests if '<e:Enumerate>' in r]),2)
        store=Store(self.database)
        try:
            states=[r['state'] for r in store.enumerations(self.url,'hall-east')]
            self.assertEqual(states,['released','complete'])
        finally:
            store.close()

    def test_display_name_does_not_substitute_source_id(self):
        self.scenario['routes']=[
            route('<e:Enumerate>', enumerate_response('ctx-1')),
            route(pull_match('ctx-1'), pull_response('g1',
                '<e:Window id="w1" hall="east"/><e:Window id="w2" hall="east"/>'
                '<e:Capability id="c1" window="w2" service="permit"/>'
                '<e:Capability id="c2" window="east" service="company-change"/>', end=True)),
        ]
        result=collect(self.url,self.database,'hall-east')
        self.assertEqual(result['state'],'missing-references')
        self.assertEqual(result['missing'],[dict(capability='c2',window='east')])

    def test_generation_change_mid_enumeration_rejected(self):
        page2=pull_response('g2', PAGE2, end=True)
        self.scenario['routes']=[
            route('<e:Enumerate>', enumerate_response('ctx-1')),
            route(pull_match('ctx-1'), pull_response('g1', PAGE1, context='ctx-2')),
            route(pull_match('ctx-2'), page2),
        ]
        with self.assertRaises(GenerationChanged):
            collect(self.url,self.database,'hall-east')
        view=self.view()
        self.assertEqual(view['state'],'reclaiming')
        self.assertEqual(view['lifecycle'],'inconsistent')
        self.assertEqual({e['key'] for e in view['entities']},{'w1','c1'})
        store=Store(self.database)
        try:
            kept=hashlib.sha256(self.url.encode()+b'\0'+page2.encode()).hexdigest()
            self.assertIsNotNone(store.receipt(kept))
        finally:
            store.close()

    def test_reclaims_exhausted_stay_reclaiming(self):
        self.scenario['routes']=[
            route('<e:Enumerate>', enumerate_response('ctx-1')),
            route(pull_match('ctx-1'), fault('ContextExpired','gone'), status=500),
        ]
        result=collect(self.url,self.database,'hall-east',max_reclaims=1)
        self.assertEqual(result['state'],'reclaiming')
        self.assertEqual(result['lifecycle'],'expired')
        self.assertEqual(len([r for r in self.requests() if '<e:Enumerate>' in r]),2)

    def test_pull_response_without_context_or_end_rejected(self):
        body=pull_response('g1', PAGE1)
        self.scenario['routes']=[
            route('<e:Enumerate>', enumerate_response('ctx-1')),
            route(pull_match('ctx-1'), body),
        ]
        with self.assertRaisesRegex(ValueError,'EndOfSequence'):
            collect(self.url,self.database,'hall-east')
        store=Store(self.database)
        try:
            kept=hashlib.sha256(self.url.encode()+b'\0'+body.encode()).hexdigest()
            self.assertIsNotNone(store.receipt(kept))
        finally:
            store.close()

if __name__=='__main__':
    unittest.main()

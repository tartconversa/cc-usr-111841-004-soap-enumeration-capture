import json
import sqlite3

class GenerationChanged(ValueError):
    """同一枚举内 generation 发生变化；两个 generation 不能拼成一份已核实配置。"""

_UNSET=object()

class Store:
    def __init__(self, path):
        self.connection=sqlite3.connect(path)
        self.connection.executescript("""
        CREATE TABLE IF NOT EXISTS documents(key TEXT PRIMARY KEY,value TEXT NOT NULL,receipt TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY,url TEXT NOT NULL,status INTEGER NOT NULL,headers TEXT NOT NULL,body BLOB NOT NULL);
        CREATE TABLE IF NOT EXISTS enumerations(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL,
            scope TEXT NOT NULL,
            generation TEXT,
            context TEXT,
            state TEXT NOT NULL,
            detail TEXT,
            updated TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS entities(
            enumeration INTEGER NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            receipt TEXT NOT NULL,
            PRIMARY KEY(enumeration,key)
        );
        """)

    def save_response(self, receipt, response, rows):
        if len({r['key'] for r in rows}) != len(rows):
            raise ValueError('duplicate keys in response')
        with self.connection:
            self.connection.execute('INSERT OR IGNORE INTO receipts VALUES(?,?,?,?,?)',
                (receipt,response.url,response.status,json.dumps(response.headers),response.body))
            for row in rows:
                self.connection.execute('INSERT OR REPLACE INTO documents VALUES(?,?,?)',
                    (row['key'],json.dumps(row['value'],ensure_ascii=False),receipt))

    def documents(self):
        return [dict(key=k,value=json.loads(v),receipt=r) for k,v,r in self.connection.execute('SELECT key,value,receipt FROM documents ORDER BY key')]

    def receipt(self, identifier):
        row=self.connection.execute('SELECT url,status,headers,body FROM receipts WHERE id=?',(identifier,)).fetchone()
        return None if row is None else dict(url=row[0],status=row[1],headers=json.loads(row[2]),body=row[3])

    def begin_enumeration(self, url, scope):
        with self.connection:
            cursor=self.connection.execute(
                "INSERT INTO enumerations(url,scope,state) VALUES(?,?,'open')",(url,scope))
        return cursor.lastrowid

    def set_enumeration(self, identifier, *, state=_UNSET, context=_UNSET, generation=_UNSET, detail=_UNSET):
        sets=[]; args=[]
        for column,value in (('state',state),('context',context),('generation',generation),('detail',detail)):
            if value is not _UNSET:
                sets.append(f'{column}=?'); args.append(value)
        if not sets:
            return
        sets.append("updated=datetime('now')")
        with self.connection:
            self.connection.execute(
                f'UPDATE enumerations SET {",".join(sets)} WHERE id=?',(*args,identifier))

    def open_enumerations(self, url, scope):
        return [dict(id=r[0],url=url,scope=scope,generation=r[1],context=r[2],state=r[3],detail=r[4])
                for r in self.connection.execute(
                    "SELECT id,generation,context,state,detail FROM enumerations "
                    "WHERE url=? AND scope=? AND state='open' ORDER BY id DESC",(url,scope))]

    def enumerations(self, url, scope):
        return [dict(id=r[0],generation=r[1],context=r[2],state=r[3],detail=r[4])
                for r in self.connection.execute(
                    'SELECT id,generation,context,state,detail FROM enumerations '
                    'WHERE url=? AND scope=? ORDER BY id',(url,scope))]

    def save_receipt(self, receipt, response):
        with self.connection:
            self.connection.execute('INSERT OR IGNORE INTO receipts VALUES(?,?,?,?,?)',
                (receipt,response.url,response.status,json.dumps(response.headers),response.body))

    def save_pull(self, enumeration, receipt, response, rows, *, generation, context, end):
        """一页 Pull 的原件、实体与上下文旋转在同一事务内落库；generation 必须自洽。"""
        if len({r['key'] for r in rows}) != len(rows):
            raise ValueError('duplicate keys in response')
        row=self.connection.execute('SELECT generation FROM enumerations WHERE id=?',(enumeration,)).fetchone()
        if row is None:
            raise ValueError('unknown enumeration')
        if row[0] is not None and generation is not None and row[0]!=generation:
            self.save_receipt(receipt,response)
            self.set_enumeration(enumeration,state='inconsistent',context=None,
                detail=f'generation changed from {row[0]} to {generation}')
            raise GenerationChanged(
                f'generation changed from {row[0]} to {generation} inside enumeration {enumeration}')
        with self.connection:
            self.connection.execute('INSERT OR IGNORE INTO receipts VALUES(?,?,?,?,?)',
                (receipt,response.url,response.status,json.dumps(response.headers),response.body))
            for r in rows:
                self.connection.execute('INSERT OR REPLACE INTO entities VALUES(?,?,?,?)',
                    (enumeration,r['key'],json.dumps(r['value'],ensure_ascii=False),receipt))
            if end:
                self.connection.execute(
                    "UPDATE enumerations SET generation=COALESCE(?,generation),context=NULL,"
                    "state='complete',updated=datetime('now') WHERE id=?",(generation,enumeration))
            else:
                self.connection.execute(
                    "UPDATE enumerations SET generation=COALESCE(?,generation),context=?,"
                    "updated=datetime('now') WHERE id=?",(generation,context,enumeration))

    def entities(self, enumeration):
        return [dict(key=k,value=json.loads(v),receipt=r) for k,v,r in self.connection.execute(
            'SELECT key,value,receipt FROM entities WHERE enumeration=? ORDER BY key',(enumeration,))]

    def configuration(self, url, scope):
        """(url,scope) 当前配置视图；只取最近一次枚举，绝不跨 generation 拼合。

        state 四态：published 已完整发布 / missing-references 尚缺引用 /
        withdrawn 因范围变化撤回 / reclaiming 正在重领（含进行中与待重开）。
        """
        row=self.connection.execute(
            'SELECT id,generation,context,state,detail FROM enumerations '
            'WHERE url=? AND scope=? ORDER BY id DESC LIMIT 1',(url,scope)).fetchone()
        if row is None:
            return None
        identifier,generation,context,lifecycle,detail=row
        entities=self.entities(identifier)
        windows={e['key'] for e in entities if e['value'].get('type')=='Window'}
        missing=[dict(capability=e['key'],window=e['value']['window'])
                 for e in entities
                 if e['value'].get('type')=='Capability'
                 and e['value'].get('window') and e['value']['window'] not in windows]
        if lifecycle=='complete':
            state='missing-references' if missing else 'published'
        elif lifecycle=='withdrawn':
            state='withdrawn'
        else:
            state='reclaiming'
        return dict(url=url,scope=scope,state=state,lifecycle=lifecycle,note=detail,
                    generation=generation,enumeration=identifier,
                    resumable=lifecycle=='open' and bool(context),
                    entities=entities,missing=missing)

    def close(self):
        self.connection.close()

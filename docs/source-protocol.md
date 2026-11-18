# 来源协议

这是独立合成的大厅资料接入项目，不包含个人资料或真实政务凭据。来源入口样例为 `/enumeration`，所有相对链接以响应 URL 为基准。来源账号、访问范围和允许访问的 origin 由调用配置提供；真实密钥不应保存在资料中。

SOAP 1.2 namespace http://www.w3.org/2003/05/soap-envelope；业务 namespace urn:hall:enumeration。POST /enumeration 的 Body 为 Enumerate(scope)、Pull(context,maxElements) 或 Release(context)。Fault Code 为 ContextExpired 或 ScopeDenied；EndOfSequence 的出现才表示枚举结束。generation 位于 PullResponse，Window/Capability 按 id 标识；Capability.window 引用 Window.id。上下文只能用于创建它的 scope。

## 已有接口

acquire.transport.fetch 保留 HTTP 状态、原始响应头与实体字节，不自动跟随重定向。一次读取最多 max_bytes，超出时报错；当前默认超时5秒，已有单次调用语义继续有效。Response.header 大小写不敏感，sha256 只描述响应实体字节，不证明业务完整性。

acquire.decode.decode(body,headers) 返回 key/value 数组，当前只解释 fixtures/response.bin 中的一份响应。acquire.store.Store 在一个本地事务中保存响应与当前 key/value，尚无协议进度和跨响应完整性解释。acquire.service.collect_one 绑定 URL、原件及已解读记录，兼容这些单次操作的返回字段。

来源回放程序支持任意 HTTP 路径、请求体条件、响应序列及字节原件，配置不等同于完整的远端系统。缺失路由不可当成协议承诺；使用者可按上述协议补充来源行为进行联调。样本内容不限定实际输入规模或异常组合。

## 枚举采集接口

acquire.soap 构造并解析 SOAP 1.2 信封：Enumerate(scope)、Pull(context,maxElements)、Release(context)；只按 namespace 识别元素，Fault 解析为 SourceFault，code 取 Subcode Value 的本地名（ContextExpired/ScopeDenied）。

acquire.enumeration.EnumerationClient 对每次调用执行真实 HTTP POST；非 2xx 且非 Fault 的响应报错，Fault 无论 HTTP 状态都按协议处理。collect(url, database, scope, ...) 完整领取一次枚举：

- Enumerate 取得上下文；Pull 成功后必须用响应中的新 EnumerationContext 继续（上下文不透明且会旋转，只能用于创建它的 scope）。
- 响应可以没有 Items；只有 EndOfSequence 表示枚举结束。既无新上下文又无 EndOfSequence 的响应是协议违例，原件保留后报错。
- ContextExpired：旧枚举作废（expired），自动重新 Enumerate（最多 max_reclaims 次）；新枚举可能遇上窗口调配而是另一个 generation。
- ScopeDenied：本次配置撤回（withdrawn）；上次权限下读到的资料保留为原件，但不冒充当前完整结果。
- generation 在同一枚举内必须一致；中途变化时保留该页原件、标记 inconsistent 并拒绝拼合（GenerationChanged）。两个 generation 的关系不能拼成一份已核实配置。
- 采集退出后默认继续未失效的已存上下文；restart=True 先 Release 旧上下文再明确重开；不再使用的上下文（重开、作废的残留）及时 Release。EndOfSequence 后上下文已被来源消费，无需 Release。
- max_pages 是单次调用的页预算；预算用尽时上下文留在库中，视图标记 resumable，下次调用继续。

Store 的 enumerations/entities 表按枚举分代保存：每次枚举记录 url、scope、generation、当前上下文与生命周期（open/complete/expired/withdrawn/released/inconsistent）；实体只属于一次枚举，跨 generation 不拼合，旧原件（receipts）保留供核对。

Store.configuration(url, scope) 只取最近一次枚举给出四态视图：published（EndOfSequence 到达且引用齐备）、missing-references（已结束但 Capability.window 引用的 Window.id 未取到）、withdrawn（范围被拒）、reclaiming（进行中或待重领）。每个实体携带 receipt，可查到该窗口能力的实际响应出处；引用只按来源 id 解析，显示名称相同不算数。

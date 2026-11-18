# 窗口配置 SOAP 枚举的失效续领

Python 3.11+ 的授权来源读取项目。当前提供单响应读取、已有结构提取和本地资料保存；协议口径见 docs/source-protocol.md。使用标准库运行，不需要安装运行依赖。

## 本地运行

启动演练来源：`python -m acquire.origin`，终端显示实际监听地址。该服务仅监听本机回环地址，记录实际 HTTP 请求，不连接政务系统。

`acquire.service.collect_one(url, database, method='GET', headers=None, body=None, max_bytes=1048576)` 读取一份响应。`python -m acquire.service` 的帮助显示位置参数；需要按来源契约传入实际 URL 与本地 SQLite 路径。来源状态非 2xx 时不写资料。

## 枚举采集

`acquire.enumeration.collect(url, database, scope, max_elements=100, max_pages=100, max_reclaims=2, restart=False, headers=None)` 执行真实 Enumerate/Pull/Release，完整领取一次窗口配置枚举；`python -m acquire.enumeration URL DATABASE SCOPE [--restart]` 可直接运行。fixtures/scenario.json 的 POST 路由给出可联调的完整流程（Enumerate→ctx-1→ctx-2→EndOfSequence→Release）。

- Pull 成功后按响应中的新 EnumerationContext 继续（上下文不透明且会旋转）；响应可以没有 Items，但只有 EndOfSequence 才表示结束。
- ContextExpired 自动重领（最多 max_reclaims 次；新枚举可能是新 generation，绝不与旧资料拼合，旧原件保留供核对）；ScopeDenied 撤回本次配置，不拿旧权限下的资料冒充当前完整结果。
- 默认继续上次未失效的已存上下文；restart=True 先 Release 旧上下文再明确重开；不再使用的上下文及时释放。max_pages 是单次调用的页预算，预算用尽时上下文留在库中，下次调用继续。
- `Store.configuration(url, scope)` 区分四种状态：published（已完整发布）、missing-references（尚缺引用）、withdrawn（因范围变化撤回）、reclaiming（正在重领/进行中）。每个实体带 receipt，可查到实际响应出处；Capability.window 只按 Window.id 解析，显示名称相同不能代替来源 ID。

测试：`python -m unittest discover -s tests -v`。
编译检查：`python -m compileall -q acquire`。
如需构建 wheel，可先安装构建依赖再执行 `python -m pip wheel --no-deps .`；日常运行和测试不需要 wheel。

## 资料

Store.documents() 返回当前键值与收据；Store.receipt(identifier) 返回当时 URL、状态、响应头和原字节。当前同 key 的新资料覆盖旧资料；同一响应重复保存不会增加相同键。

枚举采集另存 enumerations（url、scope、generation、当前上下文、生命周期 open/complete/expired/withdrawn/released/inconsistent）与 entities（按枚举分代的实体，绝不跨 generation 拼合）。Store.enumerations(url, scope) 列出历次枚举，Store.entities(enumeration) 查某次枚举的实体，旧 generation 的实体与 receipts 原件保留供核对。

fixtures/response.bin 是合成来源原始响应，scenario.json 给出可修改的本地来源路由及响应序列。可通过 responses 数组按命中次数返回不同响应，require_headers/body_contains 检查交互上下文，body_base64 表达任意字节；没有匹配的请求返回404。该回放服务不模拟未在 scenario 中配置的协议行为，复杂联调需要按协议增加对应交互或使用另一个可控来源，不能把回放缺少路由解释成真实业务结果。

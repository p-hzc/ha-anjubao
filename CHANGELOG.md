# 更新日志

版本号遵循 `主版本.次版本.修订号`。所有条目按「新增 / 修复 / 变更」归类。

## 0.9.0

真机联调（HA 2025.1.4 + 网关 AJB-G4 / 固件 AJB.N.8.4057.20250910）后，
修正了局域网通道两处与反编译结论不符的地方，并修好云端兜底的握手崩溃。

### 修复

- **局域网开关完全无法操作**。早期实现照反编译结论，把控制指令的 subject 当路由用，
  发到 `/lan/35/{gwno}/device/property_set`，并把 `properties` 拍平成 `onoff` / `level`。
  真机实测这两个做法网关都不认：**既不回包也不执行**（回读状态仍是旧值）。
  对照组把同样的指令改成 `/lan/{gwtype}/{gwno}/receive_message` + JSON 里的
  `subject`，立刻收到 `{"subject":"device/property_set_reply","code":0}`
  并推送 `device/property_report`，物理回路同步动作。
  现局域网与控制指令已全部统一走 `receive_message`，报文体与云端同构
  （`endpoints[].properties[{property_type,value}]`），`onoff` / `level` 写法删除。
  `scene/sync`、`scene/set`、`service/call` 同理。
- **设备和实体的名称没有写进 HA**。`devices/property_get` 返回的设备名是 `devname`、
  回路名是 `endpointname`，而代码读的是 `name` 与 `devname`，全部取空后退化成
  mac 后 6 位与「回路N」。同时 `_async_refresh_topology` 拿局域网设备清单**整体覆盖**
  了 HTTP 清单，把 HTTP 才有的 `roomname`（房间）和 `module`（型号）一起丢掉，
  于是设备名、区域、型号全空。现改为按 mac 合并两份清单（HTTP 提供名称/房间/型号，
  局域网提供回路名与实时属性），并对字段名做兼容。
- **单回路设备不再带「灯1」后缀**，直接显示设备名（`客厅单控` 而不是 `客厅单控 灯1`），
  多回路仍保留 `灯1 / 灯2` 区分。
- **云端 MQTT 卡在 `[Errno 9] Bad file descriptor` 无限重连**。
  `_PinnedContext.wrap_socket` 在 paho 传入 `do_handshake_on_connect=False` 时
  提前调用 `getpeercert()`，抛 `ValueError: handshake not done yet`；
  paho 把这个 `ValueError` 当成「该 Python 不支持 SNI」，拿同一个已被 ssl 模块
  `detach()` 掉的 socket 重新 wrap，于是变成 EBADF，每 60 秒刷一次日志且永远连不上。
  现改为包装后先补握手再校验证书指纹，实测 `CONNACK rc=0` 并能稳定保持连接。

## 0.8.4

修正 0.8.3 里一处**会让集成完全不可用**的错误。

### 修复

- **云端接口全部 404，配置时登录不进去**。0.8.3 把 `BASE_CLOUD` 改成了
  `https://shcp.dyajb.com`，但该域名的 443 端口只有一张 `*.dyajb.com` 证书，
  nginx 上并没有这套 API：根路径返回 403，`/api/v1/*` 与 `/mpush/*` 全部 404，
  8443 / 8080 也不通。厂商 App 1.7.2 的 dex 里该域名同样全是 `http://`，
  上游从未提供 https 入口。
  症状是登录本身成功（`plus.anjubao.net` 的 443 正常），随后
  `getUserInfo` / 家庭 / 网关 / 设备 / `appauth` 全部 404，配置流程报错退出。
  现已把 `BASE_CLOUD` 回退为 `http://`，**登录与续期仍走 HTTPS**。

### 变更

- `README` 与 `SECURITY.md` 如实写明云端业务接口只能走 HTTP、原因与影响，
  不再声称「两个域名都已升级 HTTPS」。

## 0.8.3

按 HACS 审核意见修复三条安全问题、一条资源泄漏，并收窄发布范围。

### 修复

- **凭据明文过网**：`plus.anjubao.net` 与 `shcp.dyajb.com` 都改成 HTTPS。
  之前登录体里的手机号与密码摘要、每个请求上的 accessToken、以及 refreshToken
  都是明文传输的（Base64 只是编码）。两个域名都有 WoTrus 签发、链到
  Sectigo/USERTrust 的正规证书，改协议即可。
  > **更正（0.8.4）**：`shcp.dyajb.com` 的 443 上并没有这套 API，
  > 该处改动导致集成不可用，已回退为 HTTP。只有登录与续期保留 HTTPS。
- **MQTT 不校验服务端证书**：去掉 `CERT_NONE` 与 `tls_insecure_set(True)`。
  厂商用的是自签的 1024 位 RSA 证书、按 IP 访问、证书里没有 SAN，做不了常规的
  主机名校验，因此改为双重校验：把厂商那张证书作为唯一信任锚（`CERT_REQUIRED`），
  再在握手后比对它的 SHA-256 指纹。指纹不符时**断开并停止重连**，不再静默降级。
  新增 `certs/mqttserver.crt` 与 `mqtt.SERVER_CERT_PIN`。
- **setup 失败泄漏线程与连接**：`async_setup()` 在通道建好之后才失败时
  （例如「局域网与厂商云均无法连接」），原来只抛 `ConfigEntryNotReady`，
  已启动的局域网线程与 MQTT 客户端留在后台。因为 MQTT 的 clientId 就是 userId，
  HA 每次重试都会多一个同 id 客户端，互相把对方顶下线。现在任何失败都会先在
  执行器里 `coordinator.stop()` 再抛。

### 变更

- **只发布 `light` 平台**：窗帘（`cover`）、App 场景（`scene`）、场景面板按键
  （`button`）的报文虽然按 App 字节码逐条比对过、也在本地模拟网关上验证过，
  但没在真实网关上执行过（会真的驱动电机、切换全屋灯光）。按 HACS 要求暂不加载，
  由 `const.ENABLE_COVER_SCENE = False` 控制；真机验证后改成 `True` 即全部恢复。
- 集成选项同步隐藏「创建窗帘实体 / 创建场景实体」，避免出现无效开关。
- 诊断信息增加 `platforms` 字段，说明当前加载了哪些平台。
- 新增 `scripts/verify_setup_cleanup.py`；`verify_certs.py` 增加到 9 项
  （含指纹与内置证书一致性、`CERT_REQUIRED` 断言）。

## 0.8.2

### 新增

- **自定义证书**：把 `mqttclient.crt` / `mqttclient.key` 放到 HA 配置目录下的
  `anjubao_certs/`，集成会优先使用它们并回退到内置证书。不想要内置证书的人，
  可以换成自己从 App 安装包提取的那一份。日志（debug）会打印实际使用的路径。
- `scripts/verify_certs.py`：离线校验证书选择与回退逻辑。

### 变更

- **补齐法律与合规文档**：新增 [DISCLAIMER.md](DISCLAIMER.md)（非官方声明、商标、
  反向工程范围、内置参数与证书的来源与限制、服务条款与账号风险、禁止用途、
  物理设备安全、责任限制、权利人通知下架渠道）、[PRIVACY.md](PRIVACY.md)、
  [NOTICE](NOTICE)（澄清 MIT 许可的覆盖范围）。
- README 顶部加醒目非官方声明；「限制」补设备安全警告；「许可」改写为与
  DISCLAIMER 一致的表述；赞助说明明确为自愿捐赠。
- `.gitignore` 增加逆向分析 / 抓包 / 个人数据文件的排除规则，防止误提交。
- 新增 [SECURITY.md](SECURITY.md)：报告渠道、已知安全边界（明文 HTTP、
  不校验服务端证书、局域网明文）、凭据泄露后的处置步骤。
- `scripts/verify_diagnostics.py` 里的示例手机号改为测试号段（原来用的是真实号码），
  样本目录支持用 `ANJUBAO_SAMPLE_DIR` 指定。

## 0.8.1

### 变更

- `hacs.json` 增加 `"country": "CN"`。本集成对接的是国内厂商网关，
  标注国家后 HACS 商店里中国的用户能直接看到，其他国家不会看到无关条目。

## 0.8.0

### 新增

- **诊断信息**：集成页面「⋮ → 下载诊断信息」可导出通道状态、网关型号、设备与实体统计、
  类型分布、场景列表。密码、令牌、完整手机号与 MAC 均已脱敏，报 bug 时直接贴即可。
- **Issue 模板**：Bug 报告与功能请求都会引导填写版本、通道与诊断信息。

### 修复

- 首次刷新失败时未回收 MQTT / 局域网线程，会导致 HA 反复重试并堆积连接。
- 卸载配置项时若 `hass.data` 已被清理会抛 `KeyError`，已改为安全取值。
- `anjubao.send_command` 失败时前端只显示「Unknown error」，现在会带上具体原因
  （例如「没有可用通道」「局域网指令失败」）。

### 变更

- README 重构：补上目录、徽章、诊断与反馈、更新与卸载；修正「只走云端」的过时描述。
- 赞赏二维码从 README 顶部移到文末，不再遮住项目标题。

## 0.7.0

### 新增

- **`scene` 平台**：网关场景（`scene/sync`）生成 `scene` 实体，`scene.turn_on` 即执行 `scene/set`。
- **`button` 平台**：场景面板按键生成按键实体，实体名取绑定的场景名。
- 支持按 `property_type 18` 识别按键绑定的场景，同一面板可混有开关回路与场景按键。

### 修复

- **重要**：HTTP 的 `device/pageList` 对场景面板返回 `properties: []`，
  必须用通道查询 `devicelistproperty/get` 才能拿到 `property_type 18`。
  之前因此误判为「面板没有可控内容」，现已在启动时自动补查。

## 0.6.0

### 新增

- **设备类型表**：从 App 的类型分发函数解出 30 种 type → 9 个卡片类的映射。
- **调光亮度**：type 34 支持亮度，网关 0–100 与 HA 0–255 双向换算。
- 未收录的新类型自动按开关处理，不必等集成更新。

## 0.5.0

### 新增

- **`cover` 平台**：窗帘实体，支持开 / 关 / 停。

### 修复

- 窗帘停止走 `service/call`（`service_type: 1`），不是 `property_type: 0, value: 3`；
  局域网侧控制类 subject 直接作为路由，不再统一塞进 `/receive_message`。

## 0.4.0

### 新增

- 品牌图标与 logo（`brand/`）。
- 英文与简体中文翻译，`strings.json` 改为英文源。
- HACS Action 与 hassfest 两个 CI 工作流。

## 0.3.0

首个可用版本。

### 新增

- 账号密码登录（手机号 Base64、密码 MD5，按 App 实际请求实现）。
- 设备与回路自动发现，按房间归类生成 `light` 实体。
- 云端 MQTT over TLS 通道（`appauth` 一次性票据 + 客户端证书）。
- 局域网 UDP 发现 + TCP 长连接，局域网优先、云端兜底。
- 令牌三级兜底：401/700 用 refreshToken 续期，10003 重新登录。
- `anjubao.send_command` 通用指令服务。

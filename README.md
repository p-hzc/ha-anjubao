# 安居家园 Plus for Home Assistant

> [!WARNING]
> **非官方项目。** 本项目由个人开发者维护，与安居宝（及其关联公司）**没有任何关系**，
> 未获授权、未获认可、未获支持。通过逆向分析 App 与服务器之间的通信报文实现，
> 因此**可能违反你账号的服务条款，存在账号被限制的风险**，且厂商随时可能让接口失效。
> 使用前请阅读 [免责声明与合规边界](DISCLAIMER.md) 与 [隐私说明](PRIVACY.md)。

[![HACS Default](https://img.shields.io/badge/HACS-Default-41BDF5.svg)](https://hacs.xyz)
[![HACS validation](https://github.com/p-hzc/ha-anjubao/actions/workflows/hacs.yaml/badge.svg)](https://github.com/p-hzc/ha-anjubao/actions/workflows/hacs.yaml)
[![hassfest](https://github.com/p-hzc/ha-anjubao/actions/workflows/hassfest.yaml/badge.svg)](https://github.com/p-hzc/ha-anjubao/actions/workflows/hassfest.yaml)
[![Release](https://img.shields.io/github/v/release/p-hzc/ha-anjubao)](https://github.com/p-hzc/ha-anjubao/releases)
[![License](https://img.shields.io/github/license/p-hzc/ha-anjubao)](LICENSE)

把「安居家园 Plus」App 里的设备接入 Home Assistant —— **局域网直连优先，厂商云兜底**，
不需要在家、不需要 root、不需要抓包。

- 账号密码登录，设备自动发现，按房间归类
- 灯 / 开关 / 插座 → `light`，调光模块带亮度
- 网关状态变化实时推送到 HA（`device/property_report`）
- 网络掉线自动切换通道，局域网恢复后自动切回
- 提供通用指令服务，其它未覆盖的动作都能自己发

> **`light` / `cover` / `scene` / `button` 四个平台均已启用，并且全部在真机上跑过。**
>
> 已验证规模：16 个设备 / 33 个回路 → **27 个 `light`**、**2 个 `cover`**、
> **9 个 `scene`**、**4 个 `button`**（门口4控）。
> 开关、调光、窗帘开 / 关 / 停、场景执行、面板按键均实测下发成功并回读一致。

## 目录

- [已验证环境](#已验证环境)
- [安装](#安装)
- [配置](#配置)
- [实体](#实体)
- [窗帘 / 场景 / 面板按键](#窗帘--场景--面板按键)
- [支持哪些设备类型](#支持哪些设备类型)
- [自定义指令服务](#自定义指令服务)
- [两条控制通道](#两条控制通道)
- [自定义证书](#自定义证书)
- [用小爱同学控制](#用小爱同学控制接入语音)
- [诊断与反馈](#诊断与反馈)
- [更新与卸载](#更新与卸载)
- [限制（重要）](#限制重要)
- [合规、免责与隐私](#合规免责与隐私)
- [许可](#许可)

## 已验证环境

| 项 | 值 |
|---|---|
| App 版本 | 1.7.2（未加固） |
| 网关 | AJB-G4 / 固件 AJB.N.8.4057.20250910 |
| 网关类型 | gwtype 35 |
| 设备实测 | 16 个设备 / 33 个回路 → 27 个 `light` |
| 控制链路 | 厂商云 MQTT over TLS（`218.107.12.83:1883`，服务端证书钉扎） |

实测链路（全部跑通）：账号密码登录（**HTTPS**）→ `getUserInfo` 取 userId → 家庭/网关/设备 →
`appauth` 换取一次性票据 → MQTT CONNACK rc=0 → `device/property_set` 返回 code=0 →
`device/property_report` 实时上报 → 回读状态一致。

> 登录与续期走 HTTPS；后续云端业务接口只能走 HTTP —— `shcp.dyajb.com` 的 443
> 虽然有证书，但 nginx 上没有这套 API（`/api/v1/*`、`/mpush/*` 全 404），
> App 自己也是明文。原因与影响见 [SECURITY.md](SECURITY.md)。

窗帘与场景的报文在 2026-11-06 也做了真机验证：窗帘开 / 关 / 停全部返回
`code=0` 且网关回读一致，`scene/set` 执行场景返回 `code=0` 并推送
`device/property_report`。详见 [窗帘 / 场景 / 面板按键](#窗帘--场景--面板按键)。

## 安装

### HACS（推荐）

本仓库已收录进 **HACS 默认仓库**，不需要再添加自定义存储库：

1. HACS → 搜索「安居家园」
2. 选中后安装，重启 Home Assistant

> HACS 会缓存默认仓库列表（约 5 小时刷新一次）。刚收录时可能还搜不到，
> 到 HACS → 右上角菜单 → **重新加载数据** 再试，或先用下面的手动方式。
>
> 如果你之前用「自定义存储库」添加过本仓库，**请把那条删掉**，
> 否则 HACS 里会同时出现两份同名条目。

### 手动

把 `custom_components/anjubao` 整个目录复制到 HA 配置目录的 `custom_components/` 下，重启。

依赖 `paho-mqtt`，HA 一般已自带；若报缺失，在 `configuration.yaml` 里确认已启用 MQTT 集成。

## 图标（HA < 2026.3 需要额外一步）

HA 前端把集成图标的地址写死成
`https://brands.home-assistant.io/_/{domain}/icon.png`，自定义集成没被
[home-assistant/brands](https://github.com/home-assistant/brands) 收录时，
CDN 返回的是一张 “icon not available” 占位图 ——
「设备与服务」「添加集成」、设备详情页就都是灰块。

本地 `custom_components/anjubao/brand/` 目录要到 **HA 2026.3.0** 才被读取
（此前只有 CDN 一条路）。所以在老版本上用一个小前端模块在浏览器侧换图：

```
custom_components/anjubao/brand/{icon,icon@2x,logo,logo@2x}.png   ← 图源
www/anjubao/icon.png, logo.png                                    ← /local 静态副本
www/anjubao/brand-fix.js                                          ← 替换脚本
```

`configuration.yaml`：

```yaml
frontend:
  themes: !include_dir_merge_named themes
  extra_module_url:
    - /local/anjubao/brand-fix.js
```

重启 HA 后**强制刷新浏览器**（Ctrl+Shift+R）即可看到图标。

脚本只处理 URL 同时含 `brands.home-assistant.io` 和 `/anjubao/` 的图片，
不碰其它集成；HA 升到 2026.3+ 后本地 brand 生效，脚本替换目标不存在，自然失效，
可以删掉 `extra_module_url` 那两行。

> 想彻底摆脱这个补丁，可以把 `brand/` 里的四张图按
> [brands 仓库规范](https://github.com/home-assistant/brands#readme) 提一个 PR
> （icon 256/512、logo 短边 256/512，本项目现有的四张都符合）。
> 合并后 CDN 就有图，任何 HA 版本都不用再打补丁。

## 配置

设置 → 设备与服务 → 添加集成 → 搜索「安居家园 Plus」→ 输入：

- **账号**：App 登录用的手机号（11 位明文即可）
- **密码**：App 登录密码（明文即可）

App 登录时会把手机号做 Base64、密码做 MD5 再提交，插件内部已按同样方式处理，
**你不需要自己加密**。登录成功后会自动拉取家庭 → 网关 → 设备 → 回路，按房间生成实体。

令牌管理：`accessToken` 有效期约 24 小时，到期先用 `refreshToken` 续期；
`refreshToken` 失效时自动回落到账号密码重新登录，无需人工干预。

## 实体

实体是按**回路（endpoint）的属性**判断的，不是按设备类型一刀切 ——
因为同一个面板可能既有开关键又有场景键（比如 `Panel33Holder` 的 1/2/3 是开关、4/5/6 绑场景）。

四类实体都已启用，按下表生成：

| 回路特征 | 平台 | 能力 | 本版是否启用 |
|---|---|---|---|
| 开关 / 插座（type 7、11、15、16、39、48、93、111…） | `light` | 开 / 关 | ✅ |
| 调光模块（type 34） | `light` | 开 / 关 + 亮度（网关 0–100 ↔ HA 0–255） | ✅ |
| 窗帘电机（type 10、31） | `cover` | 开 / 关 / 停 | ✅ |
| `properties[0].property_type == 18` 且 value 非空 | `button` | 按一下执行该按键绑定的场景 | ✅ |
| `scene/sync` 的 `scene_list` | `scene` | 执行 App 里的场景 | ✅ |

- 灯实体名 = 回路名（如「灯1」），设备名 = 设备名（如「餐厅三控」）
- 区域 = 房间名（如「玄关」）；未分配房间（`ROOM00`）不设置区域
- 唯一 ID = `{设备MAC}_{回路序号}`
- **类型表里没有的新型号按开关处理**，保证将来加了新设备也能直接出现实体

真想跳过的设备类型只剩这些（它们没有可控回路）：新风面板（37/101，多属性温控）、
报警主机（3）、以及没绑场景的纯展示面板。

### 实体 ID 迁移（可选）

HA 只在实体**首次注册**时按当时的名称生成 `entity_id`，之后名称变了 ID 也不会变。
早期版本因为名称字段取空，注册出来的是 `light.3ae1f5_hui_lu_1` 这种 mac 尾巴格式；
0.9.0 修好名称字段后，新装的实例会直接用 `light.ke_ting_dan_kong`。

老实例想统一成新格式，可以逐个用 HA 官方服务改，**不要删实体重加**
（那会丢设备关联与长期统计）：

```yaml
action: entity_registry/update
data:
  entity_id: light.3ae1f5_hui_lu_1
  new_entity_id: light.ke_ting_dan_kong
```

`unique_id` 基于 `{设备MAC}_{回路序号}`，不含名称，所以升级不会产生重复实体；
不改 ID 也能正常使用，只是看起来还是旧格式。

## 窗帘 / 场景 / 面板按键

三个平台在 2026-11-06 用真机（AJB-G4 / `AJB.N.8.4057.20250910`）逐个跑过：

| 动作 | 报文 | 结果 |
|---|---|---|
| 窗帘开 / 关 | `device/property_set`，`property_type 0`，1 / 0 | `code=0`，回读一致，电机动作 |
| 窗帘停 | `service/call`，`service_type 1`、`value 1` | `service/call_reply` `code=0` |
| 场景列表 | `scene/sync` | 返回 9 个场景 |
| 执行场景 | `scene/set` + `scene_uuid` | `code=0`，并推送 `device/property_report` |

`const.py` 的 `ENABLE_COVER_SCENE` 已置 `True`，集成选项里也会出现
「创建窗帘实体 / 创建场景实体」两个开关。

两个实测发现，用之前需要知道：

1. **`scene/sync` 返回的场景 `devices` 列表不一定完整。** 「回家」在
   `scene_list` 里只列了 1 个回路，实际执行时还另外打开了「餐厅四控」的回路 4。
   不要用场景定义去推断它会动哪些灯，以 `device/property_report` 为准。
2. **窗帘不报开合度。** 网关只在回路里给 0 / 1，所以窗帘是 `assumed_state`，
   界面只有「开 / 关」，没有百分比滑块；点「停」之后状态回到 unknown
   （真正停在哪儿只有电机知道）。

## 场景与面板按键

这两件事容易搞混，说明一下：

**1. 场景（`scene` 实体）** —— App 首页那些「回家/离家/睡觉」，全局一份。
来源是网关的 `scene/sync`，执行是 `scene/set`：

```yaml
service: scene.turn_on
target:
  entity_id: scene.hui_jia
```

**2. 面板按键（`button` 实体）** —— 墙上那块「门口4控」，四个键各自绑了一个场景。
数据来自 `devicelistproperty/get` 里按键的 `properties[0]`：

```json
{"property_type": 18, "value": "gohome_dffaf077798547338e4cf8bc"}
```

`property_type 18` 就是这个按键绑定场景的 uuid（App `Panel33Holder.isBinding()` 的判定）。
按下去和 App 点卡片一样，发的是 `scene/set` + 这个 uuid。

> 注意：HTTP 的 `device/pageList` 对这类面板返回 `properties: []`，
> **必须用 `devicelistproperty/get` 才拿得到绑定关系**。集成已经在启动时自动补了这一步。

所以物理面板和 HA 实体是「同源不同入口」：你在墙上按「回家」和在 HA 里激活
`scene.hui_jia`，下发的是同一个场景，不会互相冲突。

场景按键做成 `button` 而不是 `switch`，是因为它没有开/关状态 ——
做成开关只会得到一堆永远显示「开着」的假开关，还会污染语音控制。

## 支持哪些设备类型

类型表来自 App 1.7.2 的类型分发函数
`com/anjubao/smarthome/ui/adapter/WanItemAdapter.createViewHolder`（if-eq + sparse/packed-switch），
每个分支对应一个 Holder，再按各 Holder 的 `controlDevice` 判定属性号。

| type | 分类 | App 里的卡片 | HA 实体 |
|---|---|---|---|
| 7 / 11 / 15 / 48 / 93 / 111 | 多键开关 | `MultipleDevicePointHolderFour` | light（开关） |
| 16 / 39 | 智能插座 | `AllJackHolder` | light（开关） |
| 34 | 调光模块 | `DimmerHolder`（property 0 + 2） | light（亮度） |
| 10 / 31 | 窗帘电机 | `CurtainHolder`（开/关 + `service/call` 停） | cover |
| 82 / 83 / 84 / 85 / 86 / 87 | 场景面板 | `LightControlScenePanelHolder` / `PanelXXHolder` | 按键 → button，开关回路 → light |
| 78 / 80 | 一键情景按钮 | `AllOnekeyBottonHolder` | 绑场景 → button |
| 20 / 46 / 77 / 79 | 三键场景面板 | `ThreeSceneHolder2` | 绑场景 → button |
| 88 / 89 / 90 / 91 | 双联面板 | `PanelDuplexHolder` | 绑场景 → button |
| 28 | 情景/按键面板 | `NormalDeviceHolder`（只展示） | 4 个按键各绑一个场景 → 4 个 button |
| 37 / 101 | 新风面板 | `AllNewAirPanelHolder` | 不生成（多属性温控，要塞进 climate，待做） |
| 3 | 报警/安防主机 | `AllAlarmHolder` | 不生成 |
| 其它 | 未收录 | — | 按开关处理，生成 light |

面板类的最终归属不看 type，看**这个按键自身有没有绑定场景**（`property_type 18`）。
`PanelDuplexHolder` 这类在 App 里压根没有 `controlDevice` 的，如果又没绑场景，
就确实没有可控制的东西，会跳过并在日志里说明。

## 自定义指令服务

实体覆盖不了的场景（情景面板、定时、查询）通过 `anjubao.send_command` 发送：

```yaml
service: anjubao.send_command
data:
  subject: device/property_set
  payload:
    mac: "00158d00083ae1f5"
    dev_type: 7
    endpoints:
      - index: 1
        properties:
          - property_type: 0
            value: 1
```

参数说明：

| 字段 | 说明 |
|---|---|
| `subject` | 业务类型，如 `device/property_set`、`service/call`、`devicelistproperty/get` |
| `payload` | 附加字段，会合并进报文（`mac` / `dev_type` / `endpoints` / `homeid` 等） |
| `wait` | 是否等待网关回执，默认 `true`；高频操作用 `false` |
| `entry_id` | 多账号时指定配置项，留空用第一个 |

### 已知字段

- `property_type: 0` → 开关，值 `0`/`1`；**窗帘回路里同一字段表示开合：`1`=开，`0`=关**
- `property_type: 2` → 亮度，值 `0`–`100`
- `property_type: 18` → **面板按键绑定的场景 uuid**，值形如 `gohome_dffaf077798547338e4cf8bc`
- 窗帘**停止**走 `subject: service/call`，`endpoints: [{index, service_type: 1, value: 1}]`
- 场景执行走 `subject: scene/set`，报文体只有 `{"scene_uuid": "..."}`，不带 mac/dev_type

只想执行一个场景、不想建实体时：

```yaml
service: anjubao.send_command
data:
  subject: scene/set
  payload:
    scene_uuid: gohome_dffaf077798547338e4cf8bc
```

场景 uuid 从 `scene/sync` 里拿：

```yaml
service: anjubao.send_command
data:
  subject: scene/sync
```

窗帘三种动作的原始报文（App `CurtainHolder.controlDevice(value, index)` 实证）：

```yaml
# 开 / 关：value=1 / value=0，走 device/property_set
service: anjubao.send_command
data:
  subject: device/property_set
  payload:
    mac: "00158d000657e99f"
    dev_type: 31
    endpoints:
      - index: 1
        properties: [{property_type: 0, value: 1}]
```

```yaml
# 停：走 service/call，service_type 固定 1、value 固定 1
service: anjubao.send_command
data:
  subject: service/call
  payload:
    mac: "00158d000657e99f"
    dev_type: 31
    endpoints:
      - index: 1
        service_type: 1
        value: 1
```

### 常用：查询全屋状态

```yaml
service: anjubao.send_command
data:
  subject: devicelistproperty/get
```

回复会写到 HA 日志（DEBUG 级别），也可以用来排查某个回路是否在线。

## 两条控制通道

插件同时实现了 App 的两种控制方式，**局域网优先，云端兜底**：

| | 局域网直连 | 厂商云 |
|---|---|---|
| 链路 | UDP 9000 发现 → TCP 8000 长连接 | MQTT over TLS（服务端证书钉扎）|
| 延迟 | 毫秒级 | 几十到几百毫秒 |
| 依赖 | 需与网关同网段 | 只要能上网 |
| 占用 | 一条到网关的 TCP | 独占 `clientId=userId`，会顶掉手机 App |
| 何时使用 | 连得上就用 | 局域网连不上或掉线时自动切过来 |

启动顺序：先广播发现网关 → 连上就直接用局域网（不连云端，手机 App 仍可正常使用）→
局域网不可用才启用云端 MQTT。运行中局域网掉线会自动回落云端，恢复后再切回。

**网络要求**：HA 所在主机要和网关在同一网段，否则 UDP 广播出不去。
如果网关挂在二级路由后面（常见于主路由 + 光猫/旧路由两级），有两种办法：

1. 把网关网线插到主路由 LAN 口
2. 在集成选项里**手动填网关 IP**（如 `192.168.1.18`），跳过发现直接连

## 自定义证书

云端通道需要一张客户端证书做 TLS 双向握手。集成目录里自带了一份（来自厂商公开分发的
App，不含任何个人信息，说明见 [DISCLAIMER.md 第 4 节](DISCLAIMER.md)）。

如果你更希望用自己提取的证书，把两个文件放到 HA 配置目录下的 `anjubao_certs/` 里即可，
集成会**优先使用**它们，不需要改任何代码：

```
<你的 HA 配置目录>/
└── anjubao_certs/
    ├── mqttclient.crt
    └── mqttclient.key
```

两个文件必须**同时存在**且文件名一致，否则自动回退到内置证书。放好后重启 HA 生效，
日志（debug 级别）会打印实际使用的是哪一份。

## 工作原理

```
账号密码 ──登录──> accessToken / refreshToken（长期有效，自动续期）
                        │
                        ├── appauth ──> MQTT 一次性票据
                        │                   │
                        │                   └── TLS + 客户端证书 ──> 云端长连接
                        │
                        └── HTTP ──> 家庭 / 网关 / 设备清单

局域网（优先）
  发现：UDP 255.255.255.255:9000  "/lan/gateway/scan\r\n{"msg_id":1}"
  连接：TCP {网关IP}:8000，报文 = "PACKLEN:" + 4字节大端长度 + 内容
  路由：**所有业务指令都走 receive_message**，subject 放在 JSON 里
  查询：/lan/35/{gwno}/receive_message\r\n{"subject":"devices/property_get",...}
  控制：/lan/35/{gwno}/receive_message\r\n{"subject":"device/property_set","mac":...,"endpoints":[{index,properties:[{property_type,value}]}]}
  窗帘停：同上，subject 换成 service/call，endpoints=[{index,service_type:1,value:1}]
  心跳：每 5 秒一次 subject=heartbeat
  上报：/lan/35/{gwno}/send_message\r\n{"subject":"device/property_report",...}
  场景列表：/lan/35/{gwno}/receive_message\r\n{"subject":"scene/sync","msg_id":N}
  执行场景：/lan/35/{gwno}/receive_message\r\n{"subject":"scene/set","scene_uuid":"..."}

云端（兜底）
  控制：/app/req/{userId}  ──>  /cloud/35/{gwno}/receive_message
  状态：/app/resp/{userId} <──  device/property_report（网关主动上报）
```

## 用小爱同学控制（接入语音）

HA 本身不被小爱认识，中间需要一座桥。目前（2026 年）国内唯一成熟的是**巴法云**：
把 HA 实体同步到巴法云，再在米家 App 里绑定巴法账号，小爱就能控制了。

```
小爱同学 ──语音──> 米家 ──> 巴法云 ──MQTT/TCP──> HA 里的 bemfa 集成 ──> 安居家园集成 ──> 网关
```

**步骤**（HA 侧 + 手机侧）：

1. bemfa 云注册账号，拿到私钥 UID
2. HA 装 bemfa 集成（HACS 自定义存储库，填仓库地址后搜索 bemfa 安装）
   - 原版 `larry-wong/bemfa` 已停更，HA 2025.3 之后（paho-mqtt 升级到 2.x）会报错
   - 用社区修复版：`https://github.com/skddyj/bemfa` 或 `https://github.com/bemfa/bemfa_cloud_ha`
3. 集成里填 UID → 打开「选项」→ 勾选要同步给小爱的实体
4. 米家 App → 我的 → 其他平台设备 → 添加 → 搜「巴法」→ 登录巴法云账号
5. 对小爱说「打开客厅灯」

**这个集成生成的实体在语音侧怎么表现**：

| 实体 | 巴法云类型 | 小爱能做什么 |
|---|---|---|
| `light`（27 个） | 灯 / 开关 | 开、关、调亮度（调光模块） |
| `cover`（2 个） | 窗帘 | 开、关、停（无百分比） |
| `scene`（9 个） | 场景 → 被虚拟成开关 | 说「回家」「离家」即可执行 |
| `button`（4 个） | 一般不同步 | 建议不勾（与 scene 重复） |

> 巴法云会把 HA 的 `scene` 虚拟成开关，所以「回家」这种场景能直接语音触发。
> 面板按键（`button`）执行的是同一个场景，同步上去只会看到重复设备，建议不勾。
> 协议细节见 [窗帘 / 场景 / 面板按键](#窗帘--场景--面板按键)。

**语音识别能不能准，取决于实体名**。现在实体名是 App 里的原始名（`灯1`、`灯2`、`客厅单控 灯1`），
小爱对「灯1」这种识别一般。建议在 HA 里改成人话：

```
灯1  ->  客厅主灯
灯2  ->  客厅灯带
灯3  ->  餐厅吊灯
```

改完在 bemfa 集成里重新同步一次即可。

**这条链路是云到云的**：断网、巴法云或米家抽风都会失效。
所以关键自动化别放在语音链路上，放 HA 内部；语音只当「懒得掏手机」的入口。

## 诊断与反馈

设置 → 设备与服务 → 安居家园 Plus → 右上角「⋮」→ **下载诊断信息**，
会得到一个 JSON，包含：

- 当前走的通道（`lan` / `cloud` / `none`）、局域网与云端各自的连接状态
- 网关型号、固件、gwtype、gwno（MAC 与手机号已打码）
- 设备数、各类实体数、按类型的设备分布、未收录的类型号
- 每个灯实体的名称与房间，以及当前加载了哪些平台

**不包含**密码、accessToken、refreshToken、完整手机号。报 bug 时直接贴这个文件即可，
比贴一大段日志更有用。

排查问题时打开详细日志：

```yaml
# configuration.yaml
logger:
  logs:
    custom_components.anjubao: debug
```

## 更新与卸载

**HACS 安装的**：HACS → 集成 → 找到「安居家园 Plus」→ 有新版会显示更新按钮，点更新后重启。

**手动安装的**：用新版本的 `custom_components/anjubao` 整个目录覆盖旧的，重启。
只覆盖不删除配置，重启后实体和自动化都不受影响。

**卸载**：设置 → 设备与服务 → 安居家园 Plus → 删除。会一并清掉实体与设备。
如果 HACS 里还留着仓库，先在那里移除，否则下次更新会把文件装回来。

升级后如果实体数量变了（比如新版开始生成场景按键），HA 会自动补建；
反过来关掉选项（例如取消局域网直连）后旧实体会变成「不可用」，
到实体设置里手动删除即可。

## 限制（重要）

**走云端时，手机 App 和 Home Assistant 不能同时在线。**

服务端强制 `clientId == userId`，换成其他 clientId 会被静默断开。所以云端连上后，手机上打开 App 会把 HA 顶下线；HA 会自动重连，但两边会互相抢。

**走局域网时没有这个问题**（不占用云端连接），但网关是否允许多个 TCP 连接尚未验证——
如果你发现手机 App 连不上局域网，说明网关只接受单连接，这一点需要按实际情况取舍。

其他：

- 云端模式下断网就不可用；网关离线时实体显示不可用
- 只控制，不含摄像头、门禁、场景编辑；窗帘 / 场景 / 面板按键待真机验证后才开放
- 轮询间隔默认 60 秒，可在集成选项里改（最低 15 秒）；实时状态靠推送，轮询只是兜底

> [!CAUTION]
> **不要把本集成用于门锁、门禁、燃气、消防、医疗、安防报警等场景。**
> 它没有任何失效保护，断网、协议变更、证书失效都可能让指令延迟或丢失。
> 远程操作窗帘、卷帘门前，请确认运行范围内没有人、宠物或易损物品。

## 合规、免责与隐私

**本项目是非官方实现。** 与安居宝（及其关联公司）没有任何关系，未获授权、
未获认可、未获支持；「安居家园」「安居宝」等商标归其权利人所有。

它通过逆向分析 App 与服务端之间的**通信报文**实现互操作，仓库里**不含**厂商的
安装包、反编译代码、抓包原始数据或任何用户隐私数据。项目不提供、也不会提供
绕过付费、破解权限或免登录的能力，只使用**你自己的账号**访问**你自己的设备**。

需要你知道的三件事：

1. **服务条款风险**：用第三方客户端访问厂商云，很可能违反你注册时同意的服务条款，
   可能导致账号被限速、风控或封禁。这是本项目最现实的风险，作者无法替你消除。
2. **可用性风险**：厂商随时可以改协议、换证书、关接口，届时集成会突然失效且不会有通知。
3. **物理安全风险**：指令会真实改变设备状态，请自行加限位与互锁。

**不建议把本项目用于商业运营**（含包装成付费的"智能家居代接入"服务）。
MIT 许可证授予的是著作权层面的使用许可，**不能**替代厂商对你使用其服务、
凭据和商标的授权；商业使用请自行取得厂商书面授权。

完整条款见 **[DISCLAIMER.md](DISCLAIMER.md)**（含内置参数与证书的来源说明、
禁止用途、责任限制、权利人通知下架渠道）；数据处理见 **[PRIVACY.md](PRIVACY.md)**；
已知安全边界（HTTPS 与 MQTT 证书钉扎的做法、局域网仍是明文 TCP）见 **[SECURITY.md](SECURITY.md)**。

## 许可

本项目代码采用 [MIT 许可证](LICENSE)，可自由使用、修改、分发，只需保留版权与许可声明。
该许可**仅覆盖作者原创的代码与文档**，不覆盖厂商的商标、App、服务端程序，
也不覆盖 `const.py` 中的应用级参数与 `certs/` 下的客户端证书（权利归厂商，
本项目仅作互操作所需的必要引用，见 [NOTICE](NOTICE)）。

作者不提供任何担保，也不对使用后果承担责任。

---

## 请我喝杯咖啡

如果这个项目帮你省下了折腾的时间，欢迎赞助。你的支持是我继续维护和改进的动力。

赞赏属于**自愿捐赠**，不是购买软件、服务或授权，不附带任何技术支持承诺，
也与厂商无关；本项目始终免费提供，不赞助同样可以完整使用。

<img width="207" height="281" alt="赞赏码" src="https://github.com/user-attachments/assets/36d2af43-e2d8-4c44-8742-11aaf1bacd14" />
<img width="288" height="288" alt="赞赏码" src="https://github.com/user-attachments/assets/cacb26fc-38a4-4041-acf8-9e99bddd67f0" />

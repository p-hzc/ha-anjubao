# 更新日志

版本号遵循 `主版本.次版本.修订号`。所有条目按「新增 / 修复 / 变更」归类。

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

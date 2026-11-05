"""安居家园 Plus 集成常量。"""

DOMAIN = "anjubao"

# 厂商固定参数（从 App 1.7.2 反编译确认）
APP_KEY = "1364109817064538112"
APP_SECRET = "2234195B08404511BFF3BD5E23AAD8C6"
APP_VERSION = "Version:1.4.0-20230330.release"
USER_AGENT = "okhttp/4.7.2"

# 登录 / 续期走 HTTPS。plus.anjubao.net 的 443 上有完整的业务路由，
# 登录体里的手机号与密码摘要不再明文过网。
BASE_ENTRANCE = "https://plus.anjubao.net/entrance_guard/api/app"

# 云端业务接口只能走 HTTP：shcp.dyajb.com 的 443 端口虽然挂着
# *.dyajb.com 的 WoTrus 证书，但 nginx 没有为这套 API 配置 server block
# （根路径 403、/api/v1/* 与 /mpush/* 全部 404），只有 80 端口可用。
# App 1.7.2 自己的 dex 里该域名也全部是 http://，没有 https 版本。
# 这是厂商服务端的限制，不是客户端可以单方面升级的；详见 SECURITY.md。
BASE_CLOUD = "http://shcp.dyajb.com"

LOGIN_PATH = "/v2/user/login"
REFRESH_PATH = "/v2/user/refreshToken"
APPAUTH_PATH = "/mpush/v1/appauth"
USER_INFO_PATH = "/api/v1/user/userApi/getUserInfo"
HOME_LIST_PATH = "/api/v1/home/pageList"
GATEWAY_LIST_PATH = "/api/v1/gatewayInfo/list"
DEVICE_LIST_PATH = "/api/v1/device/pageList"

CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_ACCESS_TOKEN = "access_token"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_USER_ID = "user_id"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_LAN = "lan"
CONF_LAN_HOST = "lan_host"
CONF_LAN_PORT = "lan_port"
CONF_CREATE_COVERS = "create_covers"
CONF_CREATE_SCENES = "create_scenes"

DEFAULT_SCAN_INTERVAL = 60
DEFAULT_LAN = True
DEFAULT_LAN_PORT = 8000
DEFAULT_CREATE_COVERS = True
DEFAULT_CREATE_SCENES = True

# 窗帘 / 场景 / 场景按键三个平台。
# 2026-11-06 在真机（AJB-G4 / AJB.N.8.4057.20250910）上逐条验证通过：
#   窗帘开/关      -> device/property_set，property_type 0，value 1/0，回读一致
#   窗帘停         -> service/call，service_type 1、value 1，service/call_reply code=0
#   场景执行       -> scene/set，scene/set_reply code=0，并推送 device/property_report
#   场景列表       -> scene/sync，返回 9 个场景（回家/离家/睡觉/用餐/学习/娱乐/会客/起床/影视）
#   面板按键       -> 门口4控 4 个 endpoint 带 property_type 18 的场景绑定
# 全部走 /receive_message + JSON 里的 subject，与灯控一致。
ENABLE_COVER_SCENE = True

# 主题
TOPIC_PUB = "/app/req/{user_id}"
TOPIC_SUB = "/app/resp/{user_id}"

# 业务 subject
SUBJECT_PROPERTY_SET = "device/property_set"
SUBJECT_PROPERTY_GET = "devicelistproperty/get"
SUBJECT_PROPERTY_REPORT = "device/property_report"
SUBJECT_SERVICE_CALL = "service/call"
SUBJECT_SCENE_SYNC = "scene/sync"
SUBJECT_SCENE_SET = "scene/set"
SUBJECT_SCENE_SYNC_REPLY = "scene/sync_reply"

# 属性号
PROP_ONOFF = 0
PROP_LEVEL = 2
# 面板按键绑定的场景 uuid（App isBinding() 实证：properties[0].property_type == 18）
PROP_SCENE_UUID = 18

# 场景列表里的配置位（scene/sync_reply 顶层字段）
SCENE_MAX_NUM = 244

# 设备类型（完整分类表见 devtypes.py）
DEV_TYPE_CURTAIN = 31

# 窗帘动作（App CurtainHolder.onClick 实证：开=1 关=0 停=3）
CURTAIN_ACTION_CLOSE = 0
CURTAIN_ACTION_OPEN = 1
CURTAIN_ACTION_STOP = 3
# 停止走 service/call，service_type 固定 1、value 固定 1
CURTAIN_STOP_SERVICE_TYPE = 1

# 未分配房间的占位名，不应写进区域
PLACEHOLDER_ROOMS = {"ROOM00", "ROOM0", ""}

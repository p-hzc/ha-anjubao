"""设备类型表。

来源：App 1.7.2 `com/anjubao/smarthome/ui/adapter/WanItemAdapter.createViewHolder`
的类型分发（if-eq + sparse/packed-switch），以及各 Holder 的 `controlDevice`
所用属性号。`createViewHolder` 里没命中的类型落到 `NormalDeviceHolder`（无控制方法）。

分类结论：
  switch        多键开关 / 单键开关面板      -> light（onoff）
  socket        插座                        -> light（onoff）
  dimmer        调光模块（property 0 + 2）   -> light（brightness）
  curtain       窗帘电机（property 0 + service/call 停止）-> cover
  scene_panel   场景面板（onClick 触发场景，不是回路）    -> 按键绑场景的做 button，其余看属性
  scene_display 三键/双联面板（只有展示，无 controlDevice）-> 同上
  fresh_air     新风面板（多属性温控）        -> 不生成实体
  alarm         报警/安防主机                -> 不生成实体

注意：面板类最终是否生成实体，取决于**按键自身**有没有绑定场景
（properties 里的 property_type 18），而不是取决于这里的类型分类。
这里只回答「这个 type 有没有可控的 onoff 回路」，用于兜底和日志。
"""
from __future__ import annotations

from dataclasses import dataclass

CAT_SWITCH = "switch"
CAT_SOCKET = "socket"
CAT_DIMMER = "dimmer"
CAT_CURTAIN = "curtain"
CAT_SCENE_PANEL = "scene_panel"
CAT_SCENE_DISPLAY = "scene_display"
CAT_FRESH_AIR = "fresh_air"
CAT_ALARM = "alarm"

# 会生成实体（controllable）
LIGHT_CATEGORIES = {CAT_SWITCH, CAT_SOCKET, CAT_DIMMER}


@dataclass(frozen=True)
class DeviceKind:
    """一种设备类型的分类与中文含义。"""

    category: str
    label: str


DEVICE_TYPES: dict[int, DeviceKind] = {
    3: DeviceKind(CAT_ALARM, "报警/安防主机"),
    7: DeviceKind(CAT_SWITCH, "多键开关"),
    10: DeviceKind(CAT_CURTAIN, "窗帘电机"),
    11: DeviceKind(CAT_SWITCH, "多键开关"),
    15: DeviceKind(CAT_SWITCH, "多键开关"),
    16: DeviceKind(CAT_SOCKET, "智能插座"),
    20: DeviceKind(CAT_SCENE_DISPLAY, "三键场景面板"),
    28: DeviceKind(CAT_SCENE_PANEL, "情景/按键面板"),
    31: DeviceKind(CAT_CURTAIN, "窗帘电机"),
    34: DeviceKind(CAT_DIMMER, "调光模块"),
    37: DeviceKind(CAT_FRESH_AIR, "新风面板"),
    39: DeviceKind(CAT_SOCKET, "智能插座"),
    46: DeviceKind(CAT_SCENE_DISPLAY, "三键场景面板"),
    48: DeviceKind(CAT_SWITCH, "多键开关"),
    77: DeviceKind(CAT_SCENE_DISPLAY, "三键场景面板"),
    78: DeviceKind(CAT_SCENE_PANEL, "一键情景按钮"),
    79: DeviceKind(CAT_SCENE_DISPLAY, "三键场景面板"),
    80: DeviceKind(CAT_SCENE_PANEL, "一键情景按钮"),
    82: DeviceKind(CAT_SCENE_PANEL, "灯控场景面板"),
    83: DeviceKind(CAT_SCENE_PANEL, "场景面板 4 键"),
    84: DeviceKind(CAT_SCENE_PANEL, "场景面板 4 键"),
    85: DeviceKind(CAT_SCENE_PANEL, "组合面板"),
    86: DeviceKind(CAT_SCENE_PANEL, "场景面板 2 键"),
    87: DeviceKind(CAT_SCENE_PANEL, "场景面板 3 键"),
    88: DeviceKind(CAT_SCENE_DISPLAY, "双联面板"),
    89: DeviceKind(CAT_SCENE_DISPLAY, "双联面板"),
    90: DeviceKind(CAT_SCENE_DISPLAY, "双联面板"),
    91: DeviceKind(CAT_SCENE_DISPLAY, "双联面板"),
    93: DeviceKind(CAT_SWITCH, "多键开关"),
    101: DeviceKind(CAT_FRESH_AIR, "新风面板"),
    111: DeviceKind(CAT_SWITCH, "多键开关"),
}

# 表里没有的类型按开关处理：安居宝绝大多数设备都是开关类，
# 这样将来新增型号也能直接出现实体。
UNKNOWN_KIND = DeviceKind(CAT_SWITCH, "未知类型（按开关处理）")


def kind_of(dev_type: int) -> DeviceKind:
    """取设备类型分类，未收录的按开关处理。"""
    return DEVICE_TYPES.get(int(dev_type), UNKNOWN_KIND)


def is_light(dev_type: int) -> bool:
    return kind_of(dev_type).category in LIGHT_CATEGORIES


def is_dimmer(dev_type: int) -> bool:
    return kind_of(dev_type).category == CAT_DIMMER


def is_curtain(dev_type: int) -> bool:
    return kind_of(dev_type).category == CAT_CURTAIN

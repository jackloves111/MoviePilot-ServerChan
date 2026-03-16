from typing import Any, List, Dict, Tuple, Optional
import re
import threading
import time
from urllib.parse import urlencode
from enum import Enum

from app.core.event import eventmanager, Event
from app.log import logger
from app.plugins import _PluginBase
from app.utils.http import RequestUtils
from app.chain.message import MessageChain
from app.schemas.types import MessageChannel, EventType
from app.schemas.message import Notification

# 猴子补丁：拦截 MessageChain.post_medias_message
# 由于 post_medias_message 不会触发 EventType.NoticeMessage 事件，
# 导致我们无法通过事件监听获取到媒体列表回复，必须直接拦截方法。
_original_post_medias_message = MessageChain.post_medias_message

def _patched_post_medias_message(self, message: Notification, medias: list) -> None:
    try:
        channel_value = message.channel.value if hasattr(message.channel, "value") else message.channel
        web_channel_value = MessageChannel.Web.value if hasattr(MessageChannel.Web, "value") else "Web"
        
        # 如果是发给 Web 且来源是 Server酱³通知
        if str(channel_value) == str(web_channel_value) and message.source == "Server酱³通知":
            logger.info(f"Server酱³ 拦截到媒体列表回复消息: {message.title}")
            
            # 从 ServerChan 插件实例发送
            # 我们需要获取插件实例。由于插件是单例的或可以通过事件/配置找到
            # 最简单的方法是在模块级别保留一个引用，或者直接导入
            
            # 格式化列表
            items = []
            for idx, media in enumerate(medias, 1):
                item_title = media.title_year if hasattr(media, "title_year") else media.title
                item_vote = media.vote_average if hasattr(media, "vote_average") else None
                
                line = f"{idx}. {item_title}"
                if item_vote:
                    line += f" 评分：{item_vote}"
                items.append(line)
            
            text = "\n".join(items)
            title = message.title or "搜索结果"
            
            # 调用发送方法。由于这里是全局函数，我们需要引用插件实例。
            # ServerChan 插件实例在加载时会被实例化，我们可以在 init_plugin 时保存自身引用。
            if hasattr(MessageChain, "_serverchan_plugin_instance"):
                plugin_instance = MessageChain._serverchan_plugin_instance
                plugin_instance._send_message(title, text, message.userid)
                return  # 拦截后不再走原流程
    except Exception as e:
        logger.error(f"Server酱³ 拦截 post_medias_message 异常: {e}")
        
    # 继续执行原始方法
    return _original_post_medias_message(self, message, medias)

# 应用补丁
MessageChain.post_medias_message = _patched_post_medias_message

# 动态注入 ServerChan 到 MessageChannel 枚举中
if not hasattr(MessageChannel, "ServerChan"):
    # 尝试通过扩展 Enum 的 _member_map_ 来注入（Hack）
    # Pydantic 验证枚举时会检查值是否在枚举成员中
    try:
        # 创建一个新的枚举成员
        # 注意：Python Enum 的内部实现可能因版本而异，这里针对 Python 3.12+ 和 Pydantic v2 进行适配
        
        # 1. 注入属性，使得 MessageChannel.ServerChan 可访问
        # 这对于代码中直接引用 MessageChannel.ServerChan 是必须的
        # 但这不会更新 _value2member_map_，所以 Pydantic 验证仍然会失败
        
        # 2. 尝试更新 _value2member_map_ (Python < 3.11) 或 _value2member_map_ (Python 3.11+)
        # 以及 _member_map_
        
        # 定义新成员的值
        new_member_name = "ServerChan"
        new_member_value = "ServerChan"
        
        # 构造新成员 (hacky way)
        # 正常情况下 Enum 成员是单例的
        # 这里我们手动创建一个
        new_member = MessageChannel.__new__(MessageChannel)
        new_member._name_ = new_member_name
        new_member._value_ = new_member_value
        
        # 注入到类属性
        setattr(MessageChannel, new_member_name, new_member)
        
        # 注入到 _member_map_
        if hasattr(MessageChannel, '_member_map_'):
             MessageChannel._member_map_[new_member_name] = new_member
             
        # 注入到 _value2member_map_
        if hasattr(MessageChannel, '_value2member_map_'):
            MessageChannel._value2member_map_[new_member_value] = new_member
            
    except Exception as e:
        logger.warn(f"ServerChan 插件尝试注入 MessageChannel 失败: {e}")

class ServerChan(_PluginBase):
    # 插件名称
    plugin_name = "Server酱³通知"
    # 插件描述
    plugin_desc = "通过Server酱³发送消息通知，支持Bot互动"
    # 插件图标
    plugin_icon = "icons/serverchan.png"
    # 插件版本
    plugin_version = "2.1.0"
    # 插件作者
    plugin_author = "SilentReed"
    # 作者主页
    author_url = "https://github.com/SilentReed"
    # 插件配置项ID前缀
    plugin_config_prefix = "serverchan_"
    # 加载顺序
    plugin_order = 27
    # 可使用的用户级别
    auth_level = 1

    # 私有属性
    _enabled = False
    _onlyonce = False
    _uid = None
    _sckey = None
    _token = None
    _chat_id = None
    
    # 轮询线程
    _polling_thread = None
    _polling_stop_event = None
    _bot_api_base = "https://bot-go.apijia.cn/bot%s"
    _push_api_base = "https://%s.push.ft07.com/send/%s.send"

    def init_plugin(self, config: dict = None):
        # 保存自身引用到 MessageChain，供猴子补丁使用
        MessageChain._serverchan_plugin_instance = self
        
        if config:
            self._enabled = config.get("enabled")
            self._onlyonce = config.get("onlyonce")
            self._sckey = config.get("sckey")
            self._token = config.get("token")

            # 去除空白字符
            if self._sckey:
                self._sckey = self._sckey.strip()
            if self._token:
                self._token = self._token.strip()

        # 尝试自动获取UID
        self._uid = self._auto_get_uid()

        # 优先使用配置的UID，Bot模式下会自动更新
        self._chat_id = self._uid

        if self._onlyonce:
            self._onlyonce = False
            self._send_message("Server酱³通知测试", "插件已启用")
            
        # 启动轮询线程
        self.stop_service()
        if self._enabled and self._token:
            self._polling_stop_event = threading.Event()
            self._polling_thread = threading.Thread(target=self._polling)
            self._polling_thread.daemon = True
            self._polling_thread.start()
            logger.info("Server酱³ Bot消息接收服务启动")

    def _auto_get_uid(self) -> Optional[str]:
        """
        尝试自动获取UID
        """
        # 1. 尝试从SendKey解析
        if self._sckey:
            # 匹配 sctp{uid}t... 格式
            match = re.match(r'^sctp(\d+)t', self._sckey)
            if match:
                uid = match.group(1)
                logger.info(f"Server酱³ 从SendKey解析UID成功: {uid}")
                return uid

        # 2. 尝试从Bot Token获取
        if self._token:
            try:
                api_url = self._bot_api_base % self._token + "/getMe"
                res = RequestUtils().get_res(api_url)
                if res and res.status_code == 200:
                    result = res.json()
                    if result.get("ok"):
                        chat_id = result.get("result", {}).get("chat_id")
                        if chat_id:
                            logger.info(f"Server酱³ Bot自动获取UID成功: {chat_id}")
                            return str(chat_id)
            except Exception as e:
                logger.warn(f"Server酱³ Bot自动获取UID失败: {str(e)}")
        
        return None

    def _init_bot(self):
        """
        初始化Bot，获取Chat ID (兼容旧逻辑，实际已被 _auto_get_uid 替代)
        """
        if not self._chat_id:
             self._chat_id = self._auto_get_uid()

    def get_state(self) -> bool:
        if not self._enabled:
            return False
        # Bot模式
        if self._token:
            return True
        # SendKey模式，必须有UID（配置或自动获取）
        if self._sckey and (self._uid or self._auto_get_uid()):
            return True
        return False

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        pass

    def get_api(self) -> List[Dict[str, Any]]:
        pass

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        拼装插件配置页面
        """
        return [
            {
                'component': 'VForm',
                'content': [
                    # 基本设置
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'enabled',
                                            'label': '启用插件',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'onlyonce',
                                            'label': '测试插件（立即运行）',
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    # SendKey
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 12
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'sckey',
                                            'label': 'SendKey',
                                            'placeholder': 'sctp123456txxxxxxxxxxxxx',
                                            'hint': 'Server酱³ SendKey，用于普通推送',
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    # Bot Token
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'token',
                                            'label': 'Bot Token',
                                            'placeholder': 'Bot Token',
                                            'hint': 'Server酱³ Bot Token，配置后开启互动功能',
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ], {
            "enabled": False,
            "onlyonce": False,
            "sckey": "",
            "token": ""
        }

    def get_page(self) -> List[dict]:
        pass
    
    def _polling(self):
        """
        轮询获取消息
        """
        offset = 0
        long_poll_timeout = 10
        
        while not self._polling_stop_event.is_set():
            try:
                api_url = self._bot_api_base % self._token + "/getUpdates"
                values = {"timeout": long_poll_timeout, "offset": offset}
                res = RequestUtils(timeout=long_poll_timeout + 5).get_res(api_url + "?" + urlencode(values))
                
                if res and res.status_code == 200:
                    result = res.json()
                    if result.get("ok"):
                        updates = result.get("result") or []
                        for update in updates:
                            update_id = update.get("update_id")
                            offset = update_id + 1
                            message = update.get("message")
                            if not message:
                                continue
                            
                            chat_id = message.get("chat", {}).get("id") or message.get("from", {}).get("id") or message.get("chat_id")
                            text = message.get("text")
                            
                            if chat_id and text:
                                logger.info(f"Server酱³ 收到消息: {text}, chat_id: {chat_id}")
                                # 由于 MessageChannel 枚举定义中没有 ServerChan，且 Pydantic 校验严格
                                # 动态注入 Enum 成员在某些 Python/Pydantic 版本中无法生效
                                # 因此这里退而求其次，使用 MessageChannel.Web 作为代理渠道
                                # source 保持为 plugin_name，以便在日志中区分
                                MessageChain().handle_message(
                                    channel=MessageChannel.Web,
                                    source=self.plugin_name,
                                    userid=chat_id,
                                    username=str(chat_id),
                                    text=text
                                )
                else:
                    time.sleep(5)
            except Exception as e:
                logger.error(f"Server酱³ 轮询异常: {str(e)}")
                time.sleep(5)
                
            # 避免死循环占用过高
            if self._polling_stop_event.is_set():
                break

    def _send_message(self, title: str, text: str, userid: str = None) -> Optional[Tuple[bool, str]]:
        """
        发送消息
        """
        try:
            # 1. Bot模式 (Bot Token)
            if self._token:
                target_chat_id = userid or self._chat_id
                if target_chat_id:
                    api_url = self._bot_api_base % self._token + "/sendMessage"
                    data = {
                        "chat_id": target_chat_id,
                        "text": f"*{title}*\n\n{text}",
                        "parse_mode": "markdown",
                    }
                    try:
                        res = RequestUtils(headers={'Content-Type': 'application/json'}).post_res(api_url, json=data)
                        if res and res.status_code == 200:
                            result = res.json()
                            if result.get("ok"):
                                logger.info(f"Server酱³(Bot) 消息发送成功: {title}")
                                return True, "发送成功"
                            else:
                                error_msg = result.get("description", "未知错误")
                                logger.warn(f"Server酱³(Bot) 消息发送失败: {error_msg}")
                        else:
                            status = res.status_code if res else "None"
                            logger.warn(f"Server酱³(Bot) 消息发送失败，状态码: {status}")
                    except Exception as e:
                        logger.error(f"Server酱³(Bot) 网络请求异常: {e}")

            # 2. SendKey模式 (Bot失败或未配置Bot时尝试)
            if self._sckey:
                if not self._uid:
                    return False, "SendKey模式需要配置UID"
                
                url = self._push_api_base % (self._uid, self._sckey)
                data = {
                    "title": title,
                    "desp": f"{title}\n\n{text}",
                }

                logger.info(f"Server酱³(SendKey) 发送消息: {title}")
                res = RequestUtils().post_res(url, data=data)
                if res and res.status_code == 200:
                    result = res.json()
                    if result.get("code") == 0:
                        logger.info(f"Server酱³(SendKey) 消息发送成功: {title}")
                        return True, "发送成功"
                    else:
                        error_msg = result.get("message", "未知错误")
                        logger.warn(f"Server酱³(SendKey) 消息发送失败: {error_msg}")
                        return False, error_msg
                else:
                    status = res.status_code if res else "None"
                    logger.warn(f"Server酱³(SendKey) 消息发送失败，状态码: {status}")
                    return False, f"请求失败，状态码: {status}"

            if self._token:
                return False, "Bot发送失败且未配置SendKey或SendKey模式不可用"
            else:
                return False, "未配置Bot Token或SendKey"
                
        except Exception as e:
            logger.error(f"Server酱³消息发送异常: {str(e)}")
            return False, str(e)

    @eventmanager.register(EventType.NoticeMessage)
    def send(self, event: Event):
        """
        消息发送事件
        """
        if not self.get_state():
            return

        if not event.event_data:
            return
            
        # 拦截发往 Web 渠道但 source 是本插件的消息
        # 这是因为我们使用 MessageChannel.Web 作为 Bot 接收消息的代理渠道
        # MoviePilot 会将回复消息也发往 Web 渠道
        # 我们需要在这里拦截并转发给 ServerChan Bot
        
        # 尝试解析 event_data
        event_data = event.event_data
        
        logger.info(f"Server酱³ NoticeMessage 接收到原始事件数据: {type(event_data)}")
        
        # 1. 处理 Notification 对象或字典
        if isinstance(event_data, dict):
            # 可能是直接的字典
            msg_body = event_data
        elif hasattr(event_data, "to_dict"):
             # 可能是 Notification 对象
             msg_body = event_data.to_dict()
        else:
             # 未知类型，尝试直接属性访问
             msg_body = event_data

        # 打印调试日志，确认拦截逻辑是否触发
        logger.info(f"Server酱³ NoticeMessage 提取的消息体: {msg_body}")
             
        # 获取消息属性
        channel = msg_body.get("channel")
        source = msg_body.get("source")
        
        # 检查是否需要拦截转发
        # 条件：渠道是 Web，且来源是 Server酱³通知
        # 注意：MessageChannel.Web 的值是 "Web" (str)，但这里 channel 可能是 MessageChannel.Web (Enum)
        # 所以最好比较值
        
        channel_value = channel.value if hasattr(channel, "value") else channel
        web_channel_value = MessageChannel.Web.value if hasattr(MessageChannel.Web, "value") else "Web"
        
        logger.info(f"Server酱³ 拦截检查: channel={channel_value}, source={source}, plugin_name={self.plugin_name}")
        
        if str(channel_value) == str(web_channel_value) and source == self.plugin_name:
             logger.info(f"Server酱³ 拦截到回复消息: {msg_body.get('title')}")
             # 提取内容
             title = msg_body.get("title")
             text = msg_body.get("text")
             userid = msg_body.get("userid")
             
             # 如果是列表选择消息（SearchResult），MoviePilot 会发送 Notification 对象
             # 我们需要处理这种情况
             
             # 检查是否包含媒体列表
             # 在 MoviePilot V2 插件架构中，post_medias_message 会在 Notification 中附带 medias 列表
             # 但通过 Event 传递时，medias 可能被放在 note 字段中，或者通过其他方式传递
             # 实际上，EventData 中可能并不包含 medias，或者包含在 note 中
             
             # 查看日志：'message': Notification(..., title='【放牛班的春天】共找到1条相关信息，请回复对应数字选择', ...)
             # 这说明 event_data 主要是 Notification 对象
             # 对于列表消息，我们需要特殊格式化
             
             # 尝试从 event_data 获取 note，其中可能包含媒体列表
             note = msg_body.get("note")
             if note and isinstance(note, list):
                 # 这是一条媒体列表消息
                 # 使用 send_list_msg 逻辑进行格式化
                 # 但这里我们没有 send_list_msg 方法（那是 app.message.client.serverchan.ServerChan 的）
                 # 所以我们需要自己实现简单的列表格式化
                 
                 items = []
                 for idx, item in enumerate(note, 1):
                     # item 是字典
                     item_title = item.get("title") or item.get("name")
                     item_year = item.get("year")
                     item_type = item.get("type")
                     item_vote = item.get("vote_average")
                     
                     line = f"{idx}. {item_title} ({item_year})"
                     if item_vote:
                         line += f" 评分：{item_vote}"
                     items.append(line)
                 
                 text = "\n".join(items)
                 if title:
                     text = f"{title}\n\n{text}"
             
             # 如果没有 text 但有 title，可能是列表选择提示
             elif not text and title:
                 text = title
                 title = "系统通知"
                 
             # 发送给 Bot
             return self._send_message(title, text, userid)

        # 正常处理其他 NoticeMessage
        # 注意：如果上面的拦截生效了，这里就不应该再处理，除非有其他逻辑
        # 但上面的逻辑已经 return 了
        
        # 2. 处理常规的 NoticeMessage（非拦截）
        # 只有当 channel 不是 Web 或者 source 不是本插件时才处理
        # 但要注意，EventType.NoticeMessage 通常是通用的，
        # 插件通常只关心发给自己的，或者自己感兴趣的。
        # 这里我们保留之前的逻辑，但要小心不要重复发送。
        
        # 之前的逻辑是直接发送，没有判断 channel
        # 现在我们增加了拦截逻辑，应该更谨慎
        
        # 如果是发给 ServerChan 的（如果未来 Enum 注入成功）
        # 或者 source 是本插件但 channel 没被识别为 Web（理论上不会，因为我们发出去就是 Web）
        
        # 这里保留之前的通用逻辑，但加上过滤
        # 如果已经拦截处理了，上面已经 return
        
        # 如果是其他渠道的消息，我们通常不应该处理，除非是用户配置了“所有通知都推送到Server酱”
        # 但插件机制通常是：系统分发 NoticeMessage，各个插件根据配置决定是否发送
        # MoviePilot 的插件通常通过 channel 过滤，或者由用户配置决定。
        # 在 plugins.v2 架构下，send 方法接收所有 NoticeMessage
        
        # 我们只处理明确指定给 ServerChan 的（如果能识别），或者用户希望所有通知都走这里
        # 但由于我们现在的 Bot 消息伪装成了 Web，回复消息也是 Web，上面已经拦截了回复。
        # 剩下的就是系统通知了。
        
        # 提取基本信息
        title = msg_body.get("title")
        text = msg_body.get("text")
        userid = msg_body.get("userid")

        if not title and not text:
            return

        # 避免死循环：如果消息来源就是自己，且不是为了回复 Bot（上面已经处理了回复），则忽略
        if source == self.plugin_name:
            return

        # 消息类型过滤交给用户自己在通知渠道设置，插件内部不再做复杂过滤，除非有特殊需求
        # 这里遵循用户指示：简化逻辑

        logger.info(f"Server酱³ 收到系统通知: {title}")
        return self._send_message(title, text, userid)

    def stop_service(self):
        """
        退出插件
        """
        if self._polling_stop_event:
            self._polling_stop_event.set()
        if self._polling_thread and self._polling_thread.is_alive():
            self._polling_thread.join(timeout=2)
            self._polling_thread = None

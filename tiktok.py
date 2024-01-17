import gzip
import json
import logging
import re
import time
from datetime import datetime

import requests
import websocket

import config
from protobuf import tk_pb2


class TikTok:

    # 设置初始状态
    def __init__(self, url):
        self.ws_conn = None
        self.url = url
        self.room_info = None

    # 获取直播间信息
    def _get_room_info(self):
        # 设置请求参数
        payload = {}
        # 设置请求头
        headers = {
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,'
                      '*/*;q=0.8,application/signed-exchange;v=b3;q=0.9',
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) '
                          'Chrome/107.0.0.0 Safari/537.36',
            'cookie': '__ac_nonce=0638733a400869171be51',
        }
        # 设置代理
        proxies = dict(http="", https="")
        # 发送请求
        response = requests.get(self.url, headers=headers, data=payload, proxies=proxies)
        # 获取响应内容
        res_cookies = response.cookies
        ttwid = res_cookies.get_dict().get("ttwid")
        res_origin_text = response.text
        re_pattern = config.content['tiktok']['re_pattern']
        re_obj = re.compile(re_pattern)
        matches = re_obj.findall(res_origin_text)
        # 解析响应内容
        for match_text in matches:
            try:
                match_json_text = json.loads(f'"{match_text}"')
                match_json = json.loads(match_json_text)
                if match_json.get('state') is None:
                    continue
                room_id = match_json.get('state').get('roomStore').get('roomInfo').get('roomId')
                room_title = match_json.get('state').get('roomStore').get('roomInfo').get('room').get('title')
                room_user_count = match_json.get('state').get('roomStore').get('roomInfo').get('room').get(
                    'user_count_str')
                unique_id = match_json.get('state').get('userStore').get('odin').get('user_unique_id')
                avatar = match_json.get('state').get('roomStore').get('roomInfo').get('anchor').get('avatar_thumb').get(
                    'url_list')[0]
                # 保存直播间信息
                self.room_info = {
                    'url': self.url,
                    'ttwid': ttwid,
                    'room_id': room_id,
                    'room_title': room_title,
                    'room_user_count': room_user_count,
                    'unique_id': unique_id,
                    'avatar': avatar,
                }
            except Exception:
                self.room_info = None

    # 连接websocket
    def connect_web_socket(self):
        # 设置重试次数
        retryCount = config.content['retryCount']
        startCount = 0
        self._get_room_info()
        while startCount <= retryCount:
            if self.room_info is None:
                self._get_room_info()
                startCount += 1
                print(f"获取直播间({self.url})信息失败，正在重试({startCount}/{retryCount})")
            break
        # 获取直播间信息失败
        if self.room_info is None:
            logging.error(f"获取直播间({self.url})信息失败")
            return
        # 设置请求头
        now = str(time.time_ns() // 1000000)
        ws_url = config.content['tiktok']['ws_origin_url'].replace('${room_id}', self.room_info.get('room_id')).replace(
            '${unique_id}', self.room_info.get('unique_id')).replace('${now}', now)
        headers = {
            'cookie': 'ttwid=' + self.room_info.get('ttwid'),
            'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) '
                          'Chrome/108.0.0.0 Safari/537.36'
        }
        # 连接websocket
        websocket.enableTrace(False)
        self.ws_conn = websocket.WebSocketApp(ws_url,
                                              header=headers,
                                              on_message=self._on_message,
                                              on_open=self._on_open,
                                              on_error=self._on_error,
                                              on_close=self._on_close)

        self.ws_conn.run_forever(reconnect=1)

    # 向 WebSocket 服务器发送请求
    def _send_ask(self, log_id, internal_ext):
        ack_pack = tk_pb2.PushFrame()
        ack_pack.logId = log_id
        ack_pack.payloadType = internal_ext

        data = ack_pack.SerializeToString()
        self.ws_conn.send(data, opcode=websocket.ABNF.OPCODE_BINARY)

    # 接收 WebSocket 服务器的消息
    def _on_message(self, ws, message):
        # 解析消息
        msg_pack = tk_pb2.PushFrame()
        # 解析消息头
        msg_pack.ParseFromString(message)
        # 解析消息体
        decompressed_payload = gzip.decompress(msg_pack.payload)
        payload_package = tk_pb2.Response()
        payload_package.ParseFromString(decompressed_payload)
        # 处理消息
        if payload_package.needAck:
            # 发送回执
            self._send_ask(msg_pack.logId, payload_package.internalExt)
        for msg in payload_package.messagesList:
            # 弹幕消息
            if msg.method == 'WebcastChatMessage' and config.content['ChatMessage']:
                self._parse_chat_msg(msg.payload)
            # 礼物消息
            elif msg.method == "WebcastGiftMessage" and config.content['GiftMessage']:
                self._parse_gift_msg(msg.payload)
            # 点赞消息
            elif msg.method == "WebcastLikeMessage" and config.content['LikeMessage']:
                self._parse_like_msg(msg.payload)
            # 入场消息
            elif msg.method == "WebcastMemberMessage" and config.content['MemberMessage']:
                self._parse_member_msg(msg.payload)
            # case 'WebcastInRoomBannerMessage':
            # case 'WebcastRoomRankMessage':
            # case 'WebcastRoomDataSyncMessage':
            # case _:

    @staticmethod
    def _on_error(ws, error):
        logging.error(error)

    @staticmethod
    def _on_close(ws, close_status_code, close_msg):
        logging.info("Websocket closed")

    @staticmethod
    def _on_open(ws):
        logging.info("Websocket opened")

    @staticmethod
    def _parse_chat_msg(payload):
        payload_pack = tk_pb2.ChatMessage()
        payload_pack.ParseFromString(payload)
        formatted_time = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(payload_pack.eventTime))
        user_name = payload_pack.user.nickName
        content = payload_pack.content
        print(f"{formatted_time} [弹幕] {user_name}: {content}")

    @staticmethod
    def _parse_gift_msg(payload):
        payload_pack = tk_pb2.GiftMessage()
        payload_pack.ParseFromString(payload)
        formatted_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        user_name = payload_pack.user.nickName
        gift_name = payload_pack.gift.name
        gift_cnt = payload_pack.comboCount
        print(f"{formatted_time} [礼物] {user_name}: {gift_name} * {gift_cnt}")

    @staticmethod
    def _parse_like_msg(payload):
        payload_pack = tk_pb2.LikeMessage()
        payload_pack.ParseFromString(payload)
        formatted_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        user_name = payload_pack.user.nickName
        like_cnt = payload_pack.count
        print(f"{formatted_time} [点赞] {user_name}: 点赞 * {like_cnt}")

    @staticmethod
    def _parse_member_msg(payload):
        payload_pack = tk_pb2.MemberMessage()
        payload_pack.ParseFromString(payload)
        formatted_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        user_name = payload_pack.user.nickName
        print(f"{formatted_time} [入场] {user_name} 进入直播间")

    @staticmethod
    def _parse_rank_msg(payload):
        payload_pack = tk_pb2.RankMessage()
        payload_pack.ParseFromString(payload)
        formatted_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        user_name = payload_pack.user.nickName
        print(f"{formatted_time} [排行] {user_name} 进入直播间")

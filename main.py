import logging
import config
from tiktok import TikTok

if __name__ == '__main__':
    room_id = input("请输入直播间ID：")
    url = config.content['url'].format(room_id)
    logging.basicConfig(level=10, format=config.content['log']['format'])
    tk = TikTok(url)
    tk.connect_web_socket()

#!/usr/bin/env python3
import argparse
import os
import uvicorn

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='PLAVE Weverse 翻译工作台（仅本机访问）')
    parser.add_argument('--port', type=int, default=8800)
    parser.add_argument('--data-dir', help='可选数据目录；包含 QQ 令牌和浏览器登录会话')
    args = parser.parse_args()
    if args.data_dir:
        os.environ['WEVERSE_DATA_DIR'] = args.data_dir
    print(f'PLAVE 翻译工作台：http://127.0.0.1:{args.port}\n按 Control+C 停止。说明书：docs/manual.html')
    uvicorn.run('weverse_bot.app:app', host='127.0.0.1', port=args.port, log_level='warning')

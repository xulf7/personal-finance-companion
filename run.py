"""本地开发入口：python run.py（任意工作目录均可通过脚本路径调用）。"""

import argparse

from app import create_app


def main():
    parser = argparse.ArgumentParser(description="启动 Personal Finance Companion 本地开发服务")
    parser.add_argument("--port", type=int, default=5000, help="本地端口，默认 5000")
    args = parser.parse_args()
    create_app().run(host="127.0.0.1", port=args.port, debug=False, load_dotenv=False)


if __name__ == "__main__":
    main()

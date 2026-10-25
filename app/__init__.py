"""Personal Finance Companion 应用工厂。"""

from flask import Flask

from .config import Config
from .routes import pages


def create_app():
    app = Flask(__name__, instance_path=str(Config.RUNTIME_DIR))
    app.config.from_object(Config)
    app.register_blueprint(pages)
    return app

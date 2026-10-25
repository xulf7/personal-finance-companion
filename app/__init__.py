"""Personal Finance Companion 应用工厂。"""

from flask import Flask

from .cli import register_cli
from .config import Config
from .routes import pages


def create_app(test_config=None):
    app = Flask(__name__, instance_path=str(Config.RUNTIME_DIR))
    app.config.from_object(Config)
    if test_config is not None:
        app.config.update(test_config)
        app.instance_path = str(app.config["RUNTIME_DIR"])
    app.register_blueprint(pages)
    register_cli(app)
    return app

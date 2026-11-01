"""Personal Finance Companion 应用工厂。"""

from flask import Flask
from werkzeug.exceptions import HTTPException

from .api import api, http_error
from .cli import register_cli
from .config import Config
from .routes import pages
from .md_provider import provider_for_id


def create_app(test_config=None):
    app = Flask(__name__, instance_path=str(Config.RUNTIME_DIR))
    app.config.from_object(Config)
    app.config["MD_PROVIDER_ID"] = Config.provider_id()
    if test_config is not None:
        app.config.update(test_config)
        app.instance_path = str(app.config["RUNTIME_DIR"])
    provider_for_id(app.config["MD_PROVIDER_ID"])  # 仅校验配置，不导入 AKShare/联网。
    app.register_blueprint(pages)
    app.register_blueprint(api)
    app.register_error_handler(HTTPException, http_error)
    register_cli(app)
    return app

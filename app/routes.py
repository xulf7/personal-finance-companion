"""基础展示与进程健康检查，不读取业务数据或外部服务。"""

from flask import Blueprint, render_template

pages = Blueprint("pages", __name__)


@pages.get("/")
def index():
    return render_template("index.html")


@pages.get("/health")
def health():
    return {"status": "ok"}

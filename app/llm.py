"""百炼兼容接口的最小后端检查；仅在显式调用时读取配置、请求网络。"""

from dataclasses import dataclass
import json
import os
import logging
from pathlib import Path
from urllib.parse import urlsplit

from .config import Config

CONFIG_ENV = "PFC_LLM_CONFIG_FILE"
CHECK_MESSAGE = "请只回复：连接成功。"
TIMEOUT_SECONDS = 30


class LLMError(Exception):
    """只携带固定中文说明、分类与状态码，不携带原始配置/上游内容。"""

    def __init__(self, code, message, *, status=None):
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True, repr=False)
class LLMConfig:
    base_url: str
    model: str
    api_key: str


@dataclass(frozen=True, repr=False)
class CheckResult:
    reply: str
    model: str | None
    usage: dict | None


def _placeholder(value):
    lowered = value.casefold()
    return (
        lowered in {"example", "demo", "test", "xxx", "sk-xxx", "sk-...", "your-model", "your_model"}
        or (value.startswith("<") and value.endswith(">"))
        or any(part in lowered for part in (
            "your_api_key", "your-api-key", "your api key", "your_base_url", "your-base-url",
            "your_model", "your-model", "your model", "your_key", "your-key", "your key",
            "placeholder", "replace_me", "replace-me", "请填写", "替换", "示例", "真实密钥", "从百炼", "模型名称",
        ))
    )


def completion_url(base_url):
    """保留配置地址及路径，允许配置已经包含 chat/completions。"""
    if not isinstance(base_url, str) or not base_url.strip() or _placeholder(base_url):
        raise LLMError("config", "配置错误：请填写有效的 base_url。")
    base_url = base_url.strip()
    try:
        parts = urlsplit(base_url)
        if (parts.scheme != "https" or not parts.hostname or parts.username is not None
                or parts.password is not None or "?" in base_url or "#" in base_url
                or any(char in base_url for char in "<>{}")
                or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in base_url)
                or parts.port == 0):
            raise ValueError()
        if parts.hostname in {"example.com", "example.org", "example.net"} or parts.hostname.endswith(".invalid"):
            raise ValueError()
    except ValueError:
        raise LLMError("config", "配置错误：base_url 须为 HTTPS 完整地址，不得含账号密码、查询参数、片段或示例占位地址。") from None
    base_url = base_url.rstrip("/")
    return base_url if base_url.endswith("/chat/completions") else base_url + "/chat/completions"


def _config_filename(environ):
    """环境变量优先；显式空值或错误路径不回退到本机文件。"""
    if CONFIG_ENV in environ:
        filename = environ[CONFIG_ENV]
        if not isinstance(filename, str) or not filename.strip():
            raise LLMError("config", "配置错误：PFC_LLM_CONFIG_FILE 已指定但为空，须填写有效路径或取消该变量。")
        return filename.strip()
    try:
        local = json.loads((Config.PROJECT_ROOT / "config.local.json").read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raise LLMError("config", "配置错误：请在项目根目录 config.local.json 设置 llm_config_file，或设置 PFC_LLM_CONFIG_FILE。") from None
    except (OSError, ValueError, UnicodeError):
        raise LLMError("config", "本机配置错误：无法读取 config.local.json，请检查权限及 UTF-8 JSON 格式。") from None
    filename = local.get("llm_config_file") if isinstance(local, dict) else None
    if not isinstance(filename, str) or not filename.strip():
        raise LLMError("config", "本机配置错误：config.local.json 中的 llm_config_file 须为非空路径字符串。")
    return filename.strip()


def load_config(environ=None):
    """唯一配置文件读取入口。相对路径以项目根目录解释，不搜索密钥位置。"""
    filename = _config_filename(os.environ if environ is None else environ)
    try:
        path = Path(filename)
        if not path.is_absolute():
            path = Config.PROJECT_ROOT / path
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, UnicodeError):
        raise LLMError("config", "配置错误：无法读取 UTF-8 JSON，请检查配置路径、读取权限及 JSON 格式。") from None
    if not isinstance(value, dict):
        raise LLMError("config", "配置错误：JSON 须为包含 base_url、model、api_key 的对象。")
    fields = {}
    for name in ("base_url", "model", "api_key"):
        item = value.get(name)
        if not isinstance(item, str) or not item.strip() or _placeholder(item.strip()):
            raise LLMError("config", f"配置错误：{name} 须为非空字符串，不能使用示例占位值。")
        fields[name] = item.strip()
    completion_url(fields["base_url"])
    if any(ord(char) < 32 or ord(char) == 127 for char in fields["model"]):
        raise LLMError("config", "配置错误：model 不得包含控制字符。")
    if not fields["api_key"].isascii() or any(ord(char) <= 32 or ord(char) == 127 for char in fields["api_key"]):
        raise LLMError("config", "配置错误：api_key 须为不含空白的 ASCII 认证令牌。")
    return LLMConfig(**fields)


def _http_error(status):
    if status in (401, 403):
        return LLMError("authentication", "认证失败：请检查 api_key、权限及业务空间配置。", status=status)
    if status == 429:
        return LLMError("rate_limit", "请求被限流；本次不重试，请稍后自行检查额度或限流设置。", status=status)
    if status in (408, 504):
        return LLMError("timeout", "请求超时；服务端可能已经处理并计费，本次不重试。", status=status)
    if 300 <= status < 400:
        return LLMError("redirect", "接口返回重定向，已阻止跟随；请核对 base_url 完整地址。", status=status)
    if 500 <= status < 600:
        return LLMError("server", "模型服务端错误；本次不重试。", status=status)
    return LLMError("http", "模型请求失败，请核对 base_url、model 及服务配置。", status=status)


def redact(value, api_key):
    value = value.replace(api_key, "[已脱敏]")
    return "".join(char if char in "\n\t" or (ord(char) >= 32 and ord(char) != 127) else "?" for char in value)


def _parse_response(value, api_key):
    try:
        choices = value["choices"]
        reply = choices[0]["message"]["content"]
        if not isinstance(reply, str) or not reply.strip() or len(reply) > 20000:
            raise ValueError()
        model = value.get("model")
        if model is not None and (not isinstance(model, str) or not model.strip()):
            raise ValueError()
        usage = value.get("usage")
        if usage is not None:
            if not isinstance(usage, dict):
                raise ValueError()
            usage = {key: usage[key] for key in ("prompt_tokens", "completion_tokens", "total_tokens") if usage.get(key) is not None}
            if any(type(count) is not int or count < 0 for count in usage.values()):
                raise ValueError()
    except (ValueError, KeyError, IndexError, TypeError, UnicodeError, AttributeError):
        raise LLMError("response", "响应格式异常：未获得有效文本回复，或模型/用量字段格式无效。") from None
    return CheckResult(redact(reply.strip(), api_key), redact(model, api_key) if model else None, usage or None)


def _make_client(config, timeout):
    from openai import OpenAI, DefaultHttpxClient
    # SDK 的调试日志可能包含输入或上游错误体；本项目不默认记录它们。
    logging.getLogger("openai._base_client").disabled = True
    logging.getLogger("httpx").disabled = True
    logging.getLogger("httpcore").setLevel(logging.CRITICAL + 1)
    base = completion_url(config.base_url).removesuffix("/chat/completions") + "/"
    return OpenAI(api_key=config.api_key, base_url=base, organization="", project="", max_retries=0, timeout=timeout,
                  http_client=DefaultHttpxClient(timeout=timeout, follow_redirects=False))


def complete(config, messages, *, max_tokens=1024, timeout=60, client=None):
    """检查和聊天共用 SDK 请求层；仅调用者显式执行时联网。"""
    from openai import APIConnectionError, APITimeoutError, APIStatusError, APIResponseValidationError
    logging.getLogger("openai._base_client").disabled = True
    logging.getLogger("httpx").disabled = True
    logging.getLogger("httpcore").setLevel(logging.CRITICAL + 1)
    if type(max_tokens) is not int or not 1 <= max_tokens <= 4096:
        raise LLMError("config", "模型输出上限须为 1–4096 的整数。")
    owned = client is None
    try:
        client = _make_client(config, timeout) if owned else client
        response = client.chat.completions.create(
            model=config.model, messages=[{"role": message["role"], "content": redact(message["content"], config.api_key)} for message in messages], stream=False, max_tokens=max_tokens,
            extra_body={"enable_thinking": False},
        )
        return _parse_response(response.model_dump(mode="json", warnings=False), config.api_key)
    except APITimeoutError:
        raise LLMError("timeout", "模型请求超时；服务端可能已处理并计费，请明确重试，程序不会自动重试。") from None
    except APIStatusError as exc:
        raise _http_error(exc.status_code) from None
    except APIConnectionError:
        raise LLMError("network", "模型连接失败，请检查网络、代理及证书信任；程序不会自动重试。") from None
    except (APIResponseValidationError, ValueError, AttributeError, TypeError):
        raise LLMError("response", "响应格式异常：未获得有效模型回复或用量。") from None
    except LLMError:
        raise
    except Exception:
        raise LLMError("internal", "模型调用未完成，敏感异常详情已隐藏；程序不会自动重试。") from None
    finally:
        if owned and client is not None:
            client.close()


def check_connection(config, *, client=None):
    return complete(config, [{"role": "user", "content": CHECK_MESSAGE}],
                    max_tokens=64, timeout=TIMEOUT_SECONDS, client=client)

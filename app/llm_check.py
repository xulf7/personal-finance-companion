"""显式运行：python -B -m app.llm_check；普通应用不会调用本入口。"""

import sys

from .llm import LLMError, check_connection, load_config, redact


def main():
    config = None
    try:
        config = load_config()
        result = check_connection(config)
        lines = ["模型连通检查成功。", "模型回复：" + result.reply,
                 "响应模型：" + (result.model or "响应未提供模型名称（不使用配置值代替）")]
        if result.usage:
            labels = {"prompt_tokens": "输入", "completion_tokens": "输出", "total_tokens": "总计"}
            lines.append("Token 用量（服务端返回）：" + "，".join(
                f"{labels[key]} {count}" for key, count in result.usage.items()))
        else:
            lines.append("Token 用量：响应未提供，不估算。")
        print(redact("\n".join(lines), config.api_key))
        return 0
    except LLMError as exc:
        message = f"检查失败 [{exc.code}]：{exc}"
        if exc.status is not None:
            message += f"（HTTP {exc.status}）"
        print(redact(message, config.api_key) if config else message, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("检查已中断；请求可能已发出，请勿自动重试。", file=sys.stderr)
        return 130
    except Exception:
        # 不输出异常对象/堆栈，它们可能包含认证信息或私有配置。
        print("检查失败：程序未能完成检查；未输出敏感异常详情，请检查配置或联系维护者。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

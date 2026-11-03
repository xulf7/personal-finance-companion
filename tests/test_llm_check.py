"""只用 runtime/tests 内假配置和网络替身，不读取真实配置或调用模型。"""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import logging
import os
from pathlib import Path
import shutil
import httpx
import ssl
from openai import OpenAI, DefaultHttpxClient
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from app.config import Config
from app import llm, llm_check

FAKE_KEY = "sk-FAKE-ONLY-SECRET-abcdef0123456789"




def success(**changes):
    return {"choices": [{"message": {"role": "assistant", "content": "连接成功。"}}],
            "model": "qwen-response-model", "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
            **changes}


class LLMCheckTests(unittest.TestCase):
    def setUp(self):
        Config.TEST_DIR.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="t05-", dir=Config.TEST_DIR)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project_root = patch.object(Config, "PROJECT_ROOT", self.root)
        self.project_root.start()
        self.addCleanup(self.project_root.stop)
        self.path = self.root / "中文 配置.json"
        self.value = {"base_url": "https://llm.unit.test/compatible-mode/v1", "model": "qwen-plus", "api_key": FAKE_KEY}
        self.write_config()
        self.env = patch.dict(os.environ, {"PFC_LLM_CONFIG_FILE": str(self.path), "MD_PROVIDER_ID": "mock-fixed-v1"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def write_config(self, value=None, encoding="utf-8"):
        self.path.write_text(json.dumps(self.value if value is None else value, ensure_ascii=False), encoding=encoding, newline="\n")

    def cli(self, *, value=None, status=200, error=None):
        recorder = Mock()
        def handle(request):
            recorder.requests(request)
            if error:
                raise error
            return httpx.Response(status, content=value if isinstance(value, bytes) else None,
                                  json=(success() if value is None else value) if not isinstance(value, bytes) else None,
                                  headers={"Location": "https://other.unit.test/" + FAKE_KEY} if 300 <= status < 400 else {})
        def factory(config, timeout):
            return OpenAI(api_key=config.api_key, base_url=llm.completion_url(config.base_url).removesuffix('/chat/completions'),
                          max_retries=0, timeout=timeout,
                          http_client=DefaultHttpxClient(transport=httpx.MockTransport(handle), follow_redirects=False))
        out, err = io.StringIO(), io.StringIO()
        with patch.object(llm, "_make_client", side_effect=factory), redirect_stdout(out), redirect_stderr(err):
            code = llm_check.main()
        return code, out.getvalue(), err.getvalue(), recorder

    def test_sdk_request_parameters_success_and_redaction(self):
        code, out, err, recorder = self.cli()
        self.assertEqual(code, 0, err)
        recorder.requests.assert_called_once()
        request = recorder.requests.call_args.args[0]
        self.assertEqual(str(request.url), self.value['base_url'] + '/chat/completions')
        self.assertEqual(request.headers['Authorization'], 'Bearer ' + FAKE_KEY)
        self.assertEqual(json.loads(request.content), {'model':'qwen-plus', 'messages':[{'role':'user','content':'请只回复：连接成功。'}],
                                                       'stream':False,'enable_thinking':False,'max_tokens':64})
        self.assertNotIn(FAKE_KEY, request.content.decode())
        self.assertEqual(request.extensions['timeout'], {'connect':30,'read':30,'write':30,'pool':30})
        self.assertIn('qwen-response-model', out)
        self.assertIn('15', out)
        code, out, err, _ = self.cli(value=success(choices=[{'message':{'content':FAKE_KEY}}],model=FAKE_KEY))
        self.assertEqual(code, 0, err)
        self.assertNotIn(FAKE_KEY, out + err)

    def test_sdk_failures_no_retry_no_key_or_raw_error_logging(self):
        for status, label in [(401,'认证'),(403,'认证'),(429,'限流'),(500,'服务端'),(503,'服务端'),(504,'超时'),(400,'请求失败'),(302,'重定向'),(307,'重定向')]:
            with self.subTest(status=status):
                code, out, err, recorder = self.cli(status=status, value={'error':{'message':FAKE_KEY}})
                self.assertEqual(code, 1)
                self.assertIn(label, err)
                self.assertNotIn(FAKE_KEY, out + err)
                recorder.requests.assert_called_once()
        for error, label in [(httpx.ReadTimeout(FAKE_KEY),'超时'),(httpx.ConnectError(FAKE_KEY),'连接失败'),(RuntimeError(FAKE_KEY),'连接失败')]:
            code,out,err,recorder=self.cli(error=error)
            self.assertEqual(code,1)
            self.assertIn(label,err)
            self.assertNotIn(FAKE_KEY,out+err)
            recorder.requests.assert_called_once()

    def test_sdk_missing_reply_usage_invalid_response_and_size(self):
        for value in [b'not JSON', b'\xff', {}, {'error':FAKE_KEY}, success(choices=[]), success(choices=[{'message':{'content':' '}}]),
                      success(choices=[{'message':{'content':[]}}]), success(choices=[{'message':{'content':'x'*20001}}]),
                      success(usage={'total_tokens':-1}), success(usage={'total_tokens':FAKE_KEY})]:
            with self.subTest(value=type(value).__name__):
                code,out,err,recorder=self.cli(value=value)
                self.assertEqual(code,1)
                self.assertNotIn(FAKE_KEY,out+err)
                recorder.requests.assert_called_once()
        for usage in (None, {}, {'unused':FAKE_KEY}):
            code,out,err,_=self.cli(value=success(usage=usage,model=None))
            self.assertEqual(code,0,err)
            self.assertIn('不估算',out)
            self.assertIn('未提供模型名称',out)
        code,out,err,_=self.cli(value=success(usage={'prompt_tokens':12}))
        self.assertEqual(code,0,err)
        self.assertNotIn('总计',out)

    def test_default_sdk_client_has_no_retry_redirect_and_normal_tls(self):
        client=llm._make_client(llm.load_config(),30)
        try:
            self.assertEqual(client.max_retries,0)
            self.assertFalse(client._client.follow_redirects)
            self.assertEqual(client._client._transport._pool._ssl_context.verify_mode, ssl.CERT_REQUIRED)
        finally:
            client.close()

    def test_debug_logs_and_outbound_messages_do_not_expose_key(self):
        records=[]
        class Capture(logging.Handler):
            def emit(self, record): records.append(record.getMessage())
        logger=logging.getLogger();handler=Capture();logger.addHandler(handler)
        try:
            with patch.object(logging.getLogger('openai._base_client'),'disabled',False), patch.object(logging.getLogger('openai._base_client'),'level',logging.DEBUG):
                code,out,err,_=self.cli(status=400,value={'error':{'message':FAKE_KEY}})
            self.assertEqual(code,1)
            self.assertNotIn(FAKE_KEY,out+err+'\n'.join(records))
        finally:logger.removeHandler(handler)
        client=Mock()
        client.chat.completions.create.return_value.model_dump.return_value=success()
        result=llm.complete(llm.load_config(),[{'role':'user','content':'exclude '+FAKE_KEY}],client=client)
        self.assertNotIn(FAKE_KEY,str(client.chat.completions.create.call_args.kwargs['messages']))
        self.assertEqual(result.reply,'连接成功。')


    def test_missing_both_sources_never_calls_network(self):
        with patch.dict(os.environ, {}, clear=True):
            code, out, err, opener = self.cli()
        self.assertEqual(code, 1)
        self.assertIn("PFC_LLM_CONFIG_FILE", err)
        self.assertIn("config.local.json", err)
        self.assertEqual(out, "")
        opener.requests.assert_not_called()

    def test_missing_file_invalid_json_encoding_and_nonobject(self):
        for raw in (b'{"api_key": "'+FAKE_KEY.encode()+b'",', b'\xff\xfe', b'[]', b'null', b'"text"'):
            with self.subTest(raw=raw):
                self.path.write_bytes(raw)
                code, out, err, opener = self.cli()
                self.assertEqual(code, 1)
                self.assertIn("配置错误", err)
                self.assertNotIn(FAKE_KEY, out + err)
                self.assertNotIn("Traceback", err)
                opener.requests.assert_not_called()
        with patch.dict(os.environ, {"PFC_LLM_CONFIG_FILE": str(self.root / "missing.json")}):
            self.assertEqual(self.cli()[0], 1)

    def test_missing_invalid_types_empty_and_placeholder_fields(self):
        for field in self.value:
            for bad in (None, False, 12, [], {}, "", "  ", "<请填写>", "YOUR_API_KEY", "placeholder"):
                with self.subTest(field=field, bad=bad):
                    self.write_config({**self.value, field: bad})
                    code, out, err, opener = self.cli()
                    self.assertEqual(code, 1)
                    self.assertIn(field, err)
                    self.assertNotIn(FAKE_KEY, out + err)
                    opener.requests.assert_not_called()
            changed = dict(self.value)
            changed.pop(field)
            self.write_config(changed)
            self.assertEqual(self.cli()[0], 1)
        self.write_config({**self.value, "api_key": "真实密钥"})
        self.assertEqual(self.cli()[0], 1)

    def test_chinese_space_path_bom_and_safe_config_repr(self):
        self.write_config(encoding="utf-8-sig")
        config = llm.load_config()
        self.assertEqual(config.api_key, FAKE_KEY)
        self.assertNotIn(FAKE_KEY, repr(config))
        self.assertNotIn(self.value["base_url"], repr(config))
        self.assertEqual(config.model, "qwen-plus")
        relative = str(self.path.relative_to(Config.PROJECT_ROOT))
        self.assertEqual(llm.load_config({"PFC_LLM_CONFIG_FILE": relative}).api_key, FAKE_KEY)

    def test_url_validation_and_joining(self):
        for base in ("https://llm.unit.test/compatible-mode/v1", "https://llm.unit.test/compatible-mode/v1/",
                     "https://llm.unit.test/compatible-mode/v1/chat/completions", "https://llm.unit.test/compatible-mode/v1/chat/completions/"):
            self.assertEqual(llm.completion_url(base), "https://llm.unit.test/compatible-mode/v1/chat/completions")
        self.assertEqual(llm.completion_url("https://llm.unit.test/custom/v1/"), "https://llm.unit.test/custom/v1/chat/completions")
        for base in ("http://llm.unit.test/v1", "ftp://llm.unit.test/v1", "https:///v1", "https://user:pass@llm.unit.test/v1",
                     "https://user@llm.unit.test/v1", "https://llm.unit.test/v1?key="+FAKE_KEY,
                     "https://llm.unit.test/v1#"+FAKE_KEY, "https://llm.unit.test:bad/v1", "https://llm.unit.test:0/v1",
                     "https://llm.unit.test:99999/v1", "https://llm.unit.test/v1\nother", "https://llm.unit.test/my path",
                     "https://example.com/v1", "https://<workspace>/compatible-mode/v1", "https://llm.invalid/v1"):
            with self.subTest(base=base), self.assertRaises(llm.LLMError) as caught:
                llm.completion_url(base)
            self.assertNotIn(FAKE_KEY, str(caught.exception))

    def test_header_and_model_control_characters_are_rejected(self):
        for field, value in (("model", "qwen\nname"), ("api_key", "sk-abc\r\nInjected: true"),
                             ("api_key", "sk-abc\x01"), ("api_key", "sk-中文")):
            self.write_config({**self.value, field: value})
            code, _, err, opener = self.cli()
            self.assertEqual(code, 1)
            self.assertIn(field, err)
            opener.requests.assert_not_called()









    def test_import_factory_home_and_health_never_read_config_or_network(self):
        script = '''
import os
import sys
from pathlib import Path
from unittest.mock import patch
original_read = Path.read_text
def guarded_read(path, *args, **kwargs):
    if path.name == 'config.local.json' or path.resolve() == Path(sys.argv[1]).resolve():
        raise AssertionError('config read')
    return original_read(path, *args, **kwargs)
with patch.object(Path, 'read_text', new=guarded_read), patch('socket.create_connection', side_effect=AssertionError('network')), patch('socket.socket.connect', side_effect=AssertionError('network')), patch('sqlite3.connect', side_effect=AssertionError('database')):
    import app.llm
    import app.llm_check
    from app import create_app
    app = create_app({'MD_PROVIDER_ID':'mock-fixed-v1'})
    assert app.test_client().get('/').status_code == 200
    assert app.test_client().get('/health').json == {'status':'ok'}
print('lazy model modules verified')
'''
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        for configured in (True, False):
            if not configured:
                env.pop("PFC_LLM_CONFIG_FILE", None)
            run = subprocess.run([sys.executable, "-B", "-c", script, str(self.path)],
                                 cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn("verified", run.stdout)

    def test_module_command_missing_config_has_nonzero_exit_without_traceback(self):
        env = os.environ.copy()
        env.pop("PFC_LLM_CONFIG_FILE", None)
        project = self.copy_project()
        run = subprocess.run([sys.executable, "-B", "-m", "app.llm_check"], cwd=project,
                             env=env, capture_output=True, encoding="utf-8", errors="replace")
        self.assertEqual(run.returncode, 1)
        self.assertNotIn("Traceback", run.stderr)
        self.assertIn("PFC_LLM_CONFIG_FILE", run.stderr)

    def copy_project(self):
        project = self.root / "project copy"
        shutil.copytree(Path(__file__).resolve().parents[1] / "app", project / "app",
                        ignore=shutil.ignore_patterns("__pycache__"))
        return project

    def local_config(self, value=None):
        path = self.root / "config.local.json"
        path.write_text(json.dumps({"llm_config_file": str(self.path)} if value is None else value),
                        encoding="utf-8-sig", newline="\n")
        return path

    def test_local_file_fallback_and_relative_path_preserve_other_settings(self):
        local = self.local_config({"llm_config_file": self.path.name, "other_setting": {"keep": True}})
        original = local.read_bytes()
        config = llm.load_config({})
        self.assertEqual(config.api_key, FAKE_KEY)
        self.assertEqual(local.read_bytes(), original)
        with patch.dict(os.environ, {}, clear=True):
            code, out, err, opener = self.cli()
        self.assertEqual(code, 0, err)
        self.assertNotIn(FAKE_KEY, out + err)
        opener.requests.assert_called_once()

    def test_environment_wins_without_reading_local_file(self):
        local = self.local_config()
        local.write_text("invalid local JSON", encoding="utf-8")
        original = Path.read_text
        def guarded_read(path, *args, **kwargs):
            if path == local:
                raise AssertionError("environment priority must skip local file")
            return original(path, *args, **kwargs)
        with patch.object(Path, "read_text", new=guarded_read):
            self.assertEqual(llm.load_config().api_key, FAKE_KEY)

    def test_explicit_invalid_environment_does_not_fall_back(self):
        self.local_config()
        for filename in ("", "  ", str(self.root / "missing-external.json")):
            with self.subTest(filename=filename), patch.dict(os.environ, {"PFC_LLM_CONFIG_FILE": filename}):
                code, out, err, opener = self.cli()
                self.assertEqual(code, 1)
                self.assertIn("配置错误", err)
                self.assertNotIn(FAKE_KEY, out + err)
                opener.requests.assert_not_called()

    def test_invalid_local_json_fields_and_external_path_are_safe_errors(self):
        local = self.local_config()
        cases = [b'{"secret":"'+FAKE_KEY.encode()+b'",', b'\xff', b'[]', b'{}']
        cases += [json.dumps({"llm_config_file": value}).encode() for value in
                  (None, False, 42, [], "", "  ", str(self.root / "missing-external.json"))]
        for value in cases:
            with self.subTest(value=value), patch.dict(os.environ, {}, clear=True):
                local.write_bytes(value)
                code, out, err, opener = self.cli()
                self.assertEqual(code, 1)
                self.assertIn("配置错误", err)
                self.assertNotIn(FAKE_KEY, out + err)
                self.assertNotIn("Traceback", err)
                opener.requests.assert_not_called()

    def test_local_file_resolves_from_another_working_directory_in_subprocess(self):
        project = self.copy_project()
        other = self.root / "different working directory"
        other.mkdir()
        (project / "config.local.json").write_text(
            json.dumps({"llm_config_file": "../" + self.path.name}, ensure_ascii=False), encoding="utf-8-sig")
        script = '''
from contextlib import redirect_stdout
import io, json, runpy
from unittest.mock import Mock, patch
from app.config import Config
from pathlib import Path
assert Config.PROJECT_ROOT.name == 'project copy'
from types import SimpleNamespace
completion = SimpleNamespace(model_dump=lambda **kwargs: {'choices':[{'message':{'content':'OK'}}], 'model':'offline-fake'})
client = Mock()
client.chat.completions.create.return_value = completion
with patch('app.llm._make_client', return_value=client), patch('socket.create_connection', side_effect=AssertionError('network')):
    try:
        runpy.run_module('app.llm_check', run_name='__main__')
    except SystemExit as exc:
        assert exc.code == 0, exc.code
client.chat.completions.create.assert_called_once()
print('local config from different cwd verified')
'''
        env = os.environ.copy()
        env.pop("PFC_LLM_CONFIG_FILE", None)
        env["PYTHONPATH"] = str(project)
        run = subprocess.run([sys.executable, "-B", "-X", "utf8", "-c", script], cwd=other, env=env,
                             capture_output=True, encoding="utf-8")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("different cwd verified", run.stdout)
        self.assertNotIn(FAKE_KEY, run.stdout + run.stderr)

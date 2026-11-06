#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""米家(Mi Home)云控制 CLI —— 通用设备发现 + miSpec 属性读写。

纯 Python 标准库实现小米云(xiaomiio)三步登录与签名请求，不依赖第三方包：
  登录:  {account}/pass/serviceLogin → /pass/serviceLoginAuth2 → serviceLoginCallback 换 serviceToken
  业务:  {api}/app/home/device_list   设备清单
         {api}/app/home/deviceapi    miSpec 属性读/写（新协议，通用于 spec 设备）
         {api}/home/devicecmd        旧 miIO 指令（method/params，兼容老设备）
         任意路径 raw_api             逃生舱（api 命令）

配置文件发现顺序：
  1. 环境变量 MIJIA_CONFIG 指向的 json
  2. 本脚本同目录的 mijia_config.json
  3. 当前工作目录的 mijia_config.json

用法：
  python mijia.py login
  python mijia.py list
  python mijia.py read  <设备名或did> <siid> <piid> [<siid> <piid> ...]
  python mijia.py write <设备名或did> <siid> <piid> <value>
  python mijia.py quick <设备名或did> on|off        # 插座/灯常用：写 siid2/piid1 后立即读回
  python mijia.py api   <path> '<json-data>'        # 任意已签名 POST
所有命令可加 --json 输出结构化 JSON。
"""
import base64
import binascii
import hashlib
import hmac
import http.cookiejar
import json
import os
import random
import string
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DEF_ACCOUNT = "https://account.xiaomi.com"
DEF_API = "https://api.io.mi.com"
UA = "ADGS-MijiaSkill/1.0 (Python-urllib)"


class MijiaError(Exception):
    pass


def load_config():
    cands = []
    env = os.environ.get("MIJIA_CONFIG")
    if env:
        cands.append(env)
    cands.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "mijia_config.json"))
    cands.append(os.path.join(os.getcwd(), "mijia_config.json"))
    for c in cands:
        if os.path.isfile(c):
            with open(c, encoding="utf-8") as f:
                cfg = json.load(f)
            if not isinstance(cfg, dict):
                raise MijiaError("配置不是 JSON 对象: " + c)
            cfg["_config_path"] = c
            return cfg
    raise MijiaError("未找到 mijia_config.json（按顺序尝试: " + "; ".join(cands) + "）。"
                     "请按 skills/mijia-home/SKILL.md 说明创建配置。")


class MiCloud:
    def __init__(self, cfg):
        self.account = (cfg.get("account_base") or DEF_ACCOUNT).rstrip("/")
        self.api = (cfg.get("api_base") or DEF_API).rstrip("/")
        self.user = str(cfg.get("username") or "")
        self.password = str(cfg.get("password") or "")
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.opener.addheaders = [("User-Agent", UA)]
        self.ssecurity = None
        self.userId = None
        self.serviceToken = None
        self._session_file = os.path.join(os.path.dirname(cfg.get("_config_path", ".")), "mijia_session.json")
        self._load_session()

    # ---------- 登录会话缓存（真实服务对频繁登录限流；缓存 serviceToken 复用） ----------
    def _load_session(self):
        if os.environ.get("MIJIA_NO_CACHE"):
            return
        try:
            with open(self._session_file, encoding="utf-8") as f:
                s = json.load(f)
            if s.get("serviceToken") and s.get("ssecurity"):
                self.serviceToken = s["serviceToken"]
                self.ssecurity = s["ssecurity"]
                self.userId = s.get("userId")
            # 恢复设备 cookie（deviceId 等）：新设备身份认证绑定在生成确认链接的
            # 会话设备上，跨进程必须保持同一 deviceId，确认才会生效
            for c in s.get("cookies") or []:
                try:
                    self.jar.set_cookie(http.cookiejar.Cookie(
                        version=0, name=c["name"], value=c["value"], port=None,
                        port_specified=False, domain=c.get("domain") or "account.xiaomi.com",
                        domain_specified=False, domain_initial_dot=False,
                        path=c.get("path") or "/", path_specified=True, secure=False,
                        expires=None, discard=True, comment=None, comment_url=None,
                        rest={}, rfc2109=False))
                except (KeyError, TypeError):
                    continue
        except (OSError, ValueError):
            pass

    def _save_session(self):
        try:
            cookies = [{"name": c.name, "value": c.value, "domain": c.domain, "path": c.path}
                       for c in self.jar]
            tmp = self._session_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"serviceToken": self.serviceToken, "ssecurity": self.ssecurity,
                           "userId": self.userId, "cookies": cookies,
                           "saved_at": int(time.time())}, f)
            os.replace(tmp, self._session_file)
        except OSError:
            pass

    def _drop_session(self):
        self.serviceToken = None
        self.ssecurity = None
        try:
            os.remove(self._session_file)
        except OSError:
            pass

    # ---------- HTTP 基础 ----------
    @staticmethod
    def _strip(text):
        for pre in ("&&&START&&&", "&&&END&&&"):
            if text.startswith(pre):
                text = text[len(pre):]
        return text.strip()

    def _get_json(self, url, params=None):
        if params:
            u = urllib.parse.urlparse(url)
            qs = dict(urllib.parse.parse_qsl(u.query))
            qs.update(params)
            url = urllib.parse.urlunparse(u._replace(query=urllib.parse.urlencode(qs)))
        try:
            with self.opener.open(url, timeout=30) as r:
                body = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            raise MijiaError("GET %s → HTTP %d: %s" % (url, e.code, e.read()[:300]))
        try:
            return json.loads(self._strip(body))
        except ValueError:
            raise MijiaError("GET %s 返回非 JSON: %s" % (url, body[:300]))

    def _post_form(self, url, form):
        req = urllib.request.Request(
            url, data=urllib.parse.urlencode(form).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with self.opener.open(req, timeout=30) as r:
                body = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            raise MijiaError("POST %s → HTTP %d: %s" % (url, e.code, e.read()[:300]))
        try:
            return json.loads(self._strip(body))
        except ValueError:
            raise MijiaError("POST %s 返回非 JSON: %s" % (url, body[:300]))

    # ---------- 登录三步 ----------
    def _get_raw(self, url):
        """GET 原始响应（不做 JSON 解析；sts 回调返回纯文本 ok，靠 Set-Cookie 生效）"""
        try:
            with self.opener.open(url, timeout=30) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            raise MijiaError("GET %s → HTTP %d: %s" % (url[:100], e.code, e.read()[:200]))

    def login(self):
        if self.serviceToken:
            return True
        if not self.user or not self.password:
            raise MijiaError("配置缺少 username/password（%s）" % load_config()["_config_path"])
        qs = urllib.parse.urlencode({"sid": "xiaomiio"})
        step1 = self._get_json(self.account + "/pass/serviceLogin",
                               {"_json": "true", "sid": "xiaomiio", "qs": qs})
        if step1.get("code") == 0 and step1.get("location"):
            loc = step1["location"]  # 已有会话
        else:
            # 新版网页登录用 MD5 大写（2026-10-24 实测：SHA1 被 70016 拒绝）
            pwd_hash = hashlib.md5(self.password.encode()).hexdigest().upper()
            # 真实服务把防重放令牌放在 _sign 字段（2026-10-24 实测；mock 用的是 sign）
            step_sign = str(step1.get("_sign") or step1.get("sign") or "")
            form = {"sid": "xiaomiio", "_json": "true", "qs": qs, "callback": "",
                    "user": self.user, "hash": pwd_hash, "_sign": step_sign}
            step2 = self._post_form(self.account + "/pass/serviceLoginAuth2", form)
            if step2.get("code") != 0:
                raise MijiaError("登录失败(serviceLoginAuth2): code=%s desc=%s"
                                 % (step2.get("code"), step2.get("description")))
            if not step2.get("location") and step2.get("notificationUrl"):
                # securityStatus=16：新设备需身份认证——在浏览器打开链接确认后重试 login。
                # 先把本会话 cookie（deviceId）落盘：确认绑定在生成链接的设备身份上，
                # 重试时必须携带同一 deviceId。
                self._save_session()
                raise MijiaError(
                    "需要二次认证（新设备确认）。请在浏览器打开并登录后点击确认，然后重试本命令：\n  "
                    + str(step2["notificationUrl"]))
            loc = str(step2.get("location") or "")
        # 新式流程（2026-10-24 实测）：ssecurity 直接在 auth2 响应体里；GET sts 后
        # jar 里有配对的 serviceToken——两者到手即登录完成，无需再走 serviceLogin
        ssec_from_auth2 = str(step2.get("ssecurity") or "")
        for _ in range(4):
            if ssec_from_auth2 and self.serviceToken:
                self.ssecurity = ssec_from_auth2
                self._save_session()
                return True
            _q = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query)
            if _q.get("ssecurity"):
                break
            if not loc:
                break
            if "sts.api.io.mi.com" in loc:
                self._get_raw(loc)  # body 'ok'；关键是 Set-Cookie
                for c in self.jar:
                    if c.name == "serviceToken" and not self.serviceToken:
                        self.serviceToken = c.value
                    elif c.name == "userId" and not self.userId:
                        self.userId = c.value
                if ssec_from_auth2 and self.serviceToken:
                    self.ssecurity = ssec_from_auth2
                    self._save_session()
                    return True
                again = self._get_json(self.account + "/pass/serviceLogin",
                                       {"_json": "true", "sid": "xiaomiio", "qs": qs})
                if again.get("ssecurity"):
                    self.ssecurity = again["ssecurity"]
                    self.userId = str(again.get("userId") or self.userId or "")
                    if not self.serviceToken:
                        raise MijiaError("拿到 ssecurity 但 jar 中无 serviceToken，配对失败")
                    self._save_session()
                    return True
                if again.get("code") == 0 and again.get("location"):
                    loc = str(again["location"])
                    continue
                raise MijiaError("passToken 后 serviceLogin 无 ssecurity/location: "
                                 + json.dumps(again, ensure_ascii=False)[:200])
            raise MijiaError("无法识别的 location（既无 ssecurity 也非 sts 回调）: " + loc[:160])
        q = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query)
        ssec = (q.get("ssecurity") or [None])[0]
        nonce = (q.get("nonce") or [None])[0] or ""
        if not ssec:
            raise MijiaError("location 中缺少 ssecurity: " + loc[:200])
        self.ssecurity = ssec
        # 第三步：带 _sign 访问 location，换 serviceToken cookie
        bsec = base64.b64decode(ssec)
        snonce = base64.b64encode(hashlib.sha256(bsec + nonce.encode()).digest()).decode()
        sign = base64.b64encode(hmac.new(
            bsec, ("_nonce=" + nonce + "&" + urllib.parse.urlparse(loc).path).encode(),
            hashlib.sha256).digest()).decode()
        step3 = self._get_json(loc, {"_sign": sign, "_nonce": nonce, "signedNonce": snonce})
        for c in self.jar:
            if c.name == "serviceToken":
                self.serviceToken = c.value
            elif c.name == "userId":
                self.userId = c.value
        if not self.serviceToken:
            loc3 = str(step3.get("location") or "")
            q3 = urllib.parse.parse_qs(urllib.parse.urlparse(loc3).query)
            self.serviceToken = (q3.get("serviceToken") or q3.get("servicetoken") or [None])[0]
        if not self.serviceToken:
            raise MijiaError("未能获得 serviceToken，第三步返回: " + json.dumps(step3)[:300])
        self._save_session()
        return True

    # ---------- 已签名业务请求 ----------
    def _signed_post_once(self, path, form):
        req = urllib.request.Request(
            self.api + path, data=urllib.parse.urlencode(form).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "x-xiaomi-protocal-flag-cn": "1"})
        try:
            with self.opener.open(req, timeout=30) as r:
                body = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            raise MijiaError("POST %s → HTTP %d: %s" % (path, e.code, e.read()[:300]))
        try:
            return json.loads(self._strip(body))
        except ValueError:
            raise MijiaError("POST %s 返回非 JSON: %s" % (path, body[:300]))

    # ---------- 米家 App 协议（RC4 加密 + SHA1 签名，2026-10-24 对齐 micloud 0.6） ----------
    @staticmethod
    def _rc4(key: bytes, data: bytes, drop: int = 1024) -> bytes:
        S = list(range(256))
        j = 0
        for i in range(256):
            j = (j + S[i] + key[i % len(key)]) & 0xFF
            S[i], S[j] = S[j], S[i]
        i = j = 0
        out = bytearray()
        for n in range(drop + len(data)):
            i = (i + 1) & 0xFF
            j = (j + S[i]) & 0xFF
            S[i], S[j] = S[j], S[i]
            if n >= drop:
                out.append(data[n - drop] ^ S[(S[i] + S[j]) & 0xFF])
        return bytes(out)

    @staticmethod
    def _gen_nonce() -> str:
        millis = int(round(time.time() * 1000))
        b = (random.getrandbits(64) - 2 ** 63).to_bytes(8, "big", signed=True)
        part2 = int(millis / 60000)
        b += part2.to_bytes(((part2.bit_length() + 7) // 8), "big")
        return base64.b64encode(b).decode()

    @staticmethod
    def _signed_nonce(ssecurity: str, nonce: str) -> str:
        m = hashlib.sha256()
        m.update(base64.b64decode(ssecurity))
        m.update(base64.b64decode(nonce))
        return base64.b64encode(m.digest()).decode()

    @staticmethod
    def _enc_sig(url: str, method: str, signed_nonce: str, params: dict) -> str:
        sp = [str(method).upper(), url.split("com")[1].replace("/app/", "/")]
        for k, v in params.items():
            sp.append("%s=%s" % (k, v))
        sp.append(signed_nonce)
        return base64.b64encode(hashlib.sha1("&".join(sp).encode()).digest()).decode()

    @classmethod
    def _enc_params(cls, url: str, method: str, signed_nonce: str,
                    nonce: str, params: dict, ssecurity: str) -> dict:
        params = dict(params)
        params["rc4_hash__"] = cls._enc_sig(url, method, signed_nonce, params)
        for k in list(params):
            params[k] = base64.b64encode(
                cls._rc4(base64.b64decode(signed_nonce), params[k].encode())).decode()
        params["signature"] = cls._enc_sig(url, method, signed_nonce, params)
        params["ssecurity"] = ssecurity
        params["_nonce"] = nonce
        return params

    def _api_call(self, url: str, params: dict) -> dict:
        """米家 App 协议调用：RC4 加密参数 + 加密响应（对齐 micloud 0.6）"""
        self.login()
        cookies = {
            "userId": str(self.userId), "serviceToken": self.serviceToken,
            "yetAnotherServiceToken": self.serviceToken,
            "locale": "cn", "timezone": "GMT+08:00",
            "is_daylight": "0", "dst_offset": "0", "channel": "MI_APP_STORE",
            "sdkVersion": "3.8.6",
        }
        nonce = self._gen_nonce()
        snonce = self._signed_nonce(self.ssecurity, nonce)
        post = self._enc_params(url, "POST", snonce, nonce, params, self.ssecurity)
        req = urllib.request.Request(
            url, data=urllib.parse.urlencode(post).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "x-xiaomi-protocal-flag-cli": "PROTOCAL-HTTP2",
                     "MIOT-ENCRYPT-ALGORITHM": "ENCRYPT-RC4",
                     "Accept-Encoding": "identity",
                     "User-Agent": ("Android-7.1.1-1.0.0-ONEPLUS A3010-136-"
                                    "ADBDEFABCDEF0 APP/xiaomi.smarthome APPV/62830"),
                     "Cookie": "; ".join("%s=%s" % kv for kv in cookies.items())})
        try:
            with self.opener.open(req, timeout=30) as r:
                body = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            raise MijiaError("POST %s → HTTP %d: %s" % (url, e.code, e.read()[:300]))
        # 响应为 RC4 密文（key=signed_nonce(ssecurity, _nonce)）
        key = self._signed_nonce(self.ssecurity, post["_nonce"])
        try:
            plain = self._rc4(base64.b64decode(key), base64.b64decode(body))
            return json.loads(plain.decode("utf-8", "replace"))
        except (ValueError, binascii.Error):
            # 非 base64/解密失败 → 可能是明文 JSON 错误响应
            try:
                return json.loads(self._strip(body))
            except ValueError:
                raise MijiaError("POST %s 响应无法解密: %s" % (url, body[:300]))

    def _signed_post(self, path, payload):
        data = payload if isinstance(payload, str) else json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"))
        params = {"data": data}
        return self._api_call("https://api.io.mi.com" + path, params)

    # ---------- 业务 API ----------
    def device_list(self):
        r = self._signed_post("/app/home/device_list", {"dids": [], "snapshot": []})
        if r.get("code") != 0:
            raise MijiaError("device_list 失败: " + json.dumps(r, ensure_ascii=False)[:300])
        result = r.get("result") or {}
        lst = result.get("list") if isinstance(result, dict) else result
        return lst or []

    def spec_read(self, did, specs):
        return self._signed_post("/app/home/deviceapi", {
            "command": "read", "did": did,
            "miSpec": json.dumps(specs, separators=(",", ":"))})

    def spec_write(self, did, specs):
        return self._signed_post("/app/home/deviceapi", {
            "command": "write", "did": did,
            "miSpec": json.dumps(specs, separators=(",", ":"))})

    def devicecmd(self, did, method, params):
        return self._signed_post("/home/devicecmd", {
            "id": int(time.time() * 1000) % 1000000,
            "method": method, "params": params, "did": did})

    def raw_api(self, path, payload):
        return self._signed_post(path, payload)


def parse_value(text):
    t = str(text).strip()
    low = t.lower()
    if low == "true" or low == "on":
        return True
    if low == "false" or low == "off":
        return False
    try:
        return int(t)
    except ValueError:
        pass
    try:
        return float(t)
    except ValueError:
        pass
    return t


def resolve_device(devices, key):
    key = str(key).strip()
    for d in devices:
        if str(d.get("did")) == key:
            return d
    hits = [d for d in devices if key.lower() in str(d.get("name", "")).lower()]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise MijiaError("未找到设备 '%s'。当前设备: %s"
                         % (key, "; ".join("%s(did=%s)" % (d.get("name"), d.get("did")) for d in devices)))
    raise MijiaError("设备名 '%s' 有歧义，请用 did 指定: %s"
                     % (key, "; ".join("%s(did=%s)" % (d.get("name"), d.get("did")) for d in hits)))


def cmd_specs(args):
    if len(args) < 2 or len(args) % 2 != 0:
        raise MijiaError("read 需要 <设备> <siid> <piid> [...] 成对参数")
    specs = []
    for i in range(0, len(args), 2):
        specs.append({"siid": int(args[i]), "piid": int(args[i + 1])})
    return specs


def main(argv):
    args = [a for a in argv if a != "--json"]
    as_json = "--json" in argv
    if not args:
        print(__doc__)
        return 2
    cmd, rest = args[0], args[1:]
    cfg = load_config()
    cloud = MiCloud(cfg)
    try:
        if cmd == "login":
            cloud.login()
            out = {"ok": True, "userId": cloud.userId,
                   "account_base": cloud.account, "api_base": cloud.api,
                   "config": cfg["_config_path"]}
        elif cmd == "list":
            devices = cloud.device_list()
            out = {"ok": True, "count": len(devices),
                   "devices": [{"name": d.get("name"), "did": d.get("did"),
                                "model": d.get("model"),
                                "online": d.get("isOnline", d.get("online"))}
                               for d in devices]}
        elif cmd == "read":
            if len(rest) < 3:
                raise MijiaError("用法: read <设备名或did> <siid> <piid> [...]")
            devices = cloud.device_list()
            dev = resolve_device(devices, rest[0])
            r = cloud.spec_read(dev["did"], cmd_specs(rest[1:]))
            out = {"ok": r.get("code") == 0, "device": dev.get("name"), "did": dev.get("did"),
                   "response": r}
        elif cmd == "write":
            if len(rest) != 4:
                raise MijiaError("用法: write <设备名或did> <siid> <piid> <value>")
            devices = cloud.device_list()
            dev = resolve_device(devices, rest[0])
            specs = [{"siid": int(rest[1]), "piid": int(rest[2]), "value": parse_value(rest[3])}]
            r = cloud.spec_write(dev["did"], specs)
            out = {"ok": r.get("code") == 0, "device": dev.get("name"), "did": dev.get("did"),
                   "wrote": specs[0], "response": r}
        elif cmd == "quick":
            if len(rest) != 2 or rest[1].lower() not in ("on", "off"):
                raise MijiaError("用法: quick <设备名或did> on|off")
            devices = cloud.device_list()
            dev = resolve_device(devices, rest[0])
            want = rest[1].lower() == "on"
            before = cloud.spec_read(dev["did"], [{"siid": 2, "piid": 1}])
            wr = cloud.spec_write(dev["did"], [{"siid": 2, "piid": 1, "value": want}])
            after = cloud.spec_read(dev["did"], [{"siid": 2, "piid": 1}])
            out = {"ok": True, "device": dev.get("name"), "did": dev.get("did"),
                   "target": want, "before": before, "write_response": wr, "after": after}
        elif cmd == "api":
            if len(rest) != 2:
                raise MijiaError("用法: api <path> '<json-data>'")
            out = {"ok": True, "response": cloud.raw_api(rest[0], rest[1])}
        else:
            raise MijiaError("未知命令: %s\n%s" % (cmd, __doc__))
    except MijiaError as e:
        out = {"ok": False, "error": str(e)}
    if as_json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        if out.get("ok"):
            print("[mijia] OK")
        else:
            print("[mijia] 失败:", out.get("error"))
        print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

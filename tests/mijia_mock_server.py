#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""米家云仿真服务（ADGS 实验）—— 模拟小米云三步登录 + 签名业务接口。

用途：让 ADGS 种群在不接触真实小米账号的情况下，按真实协议链路演练
「设备发现 → miSpec 读 → miSpec 写 → 读回验证」。所有请求写入日志，
作为事后核对 worker 汇报真伪的地面真值。

端点：
  GET  /pass/serviceLogin                     → &&&START&&& + {code:70016, sign}
  POST /pass/serviceLoginAuth2                → 校验用户名+sha1(密码) → {code:0, location(含 ssecurity/nonce)}
  GET  /pass/serviceLoginCallback             → 校验 userId cookie + _sign → Set-Cookie serviceToken
  POST /app/home/device_list                  → 签名请求，设备清单（不含属性值）
  POST /app/home/deviceapi                    → 签名请求，miSpec read/write（真正改状态）
  POST /home/devicecmd                        → 签名请求，旧 miIO method（get_prop/set_power）
  GET  /mock/state?admin=<token>              → 管理读状态
  POST /mock/set_prop  (json, admin 字段)     → 管理改属性（实验场景控制）

用法：
  python tests/mijia_mock_server.py --port 18963 \
      --state data/mijia_mock/state.json --admin-token <tok>
默认设备（首次建态）：测试插座（断电）+ 客厅人体存在传感器（有人）。
"""
import argparse
import base64
import hashlib
import json
import os
import random
import string
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MOCK_USER = "mock-user@example.com"
MOCK_PASS = "mock-pass-123"
MOCK_USERID = "208230148"

DEFAULT_STATE = {
    "devices": [
        {"did": "1187034261", "name": "测试插座", "model": "cuco.plug.cp1md",
         "isOnline": True,
         "props": {"2.1": False, "2.2": 0.0, "3.1": 25.4},
         "prop_types": {"2.1": "bool", "2.2": "float", "3.1": "float"}},
        {"did": "223344556677", "name": "客厅人体存在传感器", "model": "lumi.presence.acn001",
         "isOnline": True,
         "props": {"2.1": True, "2.2": 128, "3.1": 0},
         "prop_types": {"2.1": "bool", "2.2": "int", "3.1": "int"}},
    ]
}

PROP_HINTS = {  # 供人类阅读的属性名映射（管理端点/日志用，不进业务响应）
    "cuco.plug.cp1md": {"2.1": "power(电源)", "2.2": "power_w(功率)", "3.1": "temperature(温度)"},
    "lumi.presence.acn001": {"2.1": "presence(有人)", "2.2": "illuminance(照度)", "3.1": "no_motion_sec(无人持续秒)"},
}

_lock = threading.Lock()
_LOG = None
_STATE_PATH = None
_ADMIN = None
_state = None


def log(msg):
    line = "%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    with _lock:
        with open(_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def save_state():
    with _lock:
        tmp = _STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _STATE_PATH)


def load_state(path):
    global _state
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            _state = json.load(f)
    else:
        _state = json.loads(json.dumps(DEFAULT_STATE))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        save_state()


def find_device(did):
    for d in _state["devices"]:
        if str(d["did"]) == str(did):
            return d
    return None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "MiHomeMock/1.0"

    def log_message(self, fmt, *a):  # 静默默认访问日志，统一走自己的日志
        pass

    # ---------- helpers ----------
    def _send(self, code, body, set_cookie=None):
        payload = (body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        if set_cookie:
            for ck in set_cookie:
                self.send_header("Set-Cookie", ck)
        self.end_headers()
        self.wfile.write(payload)

    def _jsonp(self, code, obj, set_cookie=None):
        self._send(code, "&&&START&&&" + json.dumps(obj, ensure_ascii=False), set_cookie)

    def _cookies(self):
        raw = self.headers.get("Cookie") or ""
        out = {}
        for part in raw.split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                out[k.strip()] = v.strip()
        return out

    def _form(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8", "replace") if length else ""
        return dict(urllib.parse.parse_qsl(body))

    # ---------- 路由 ----------
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        if u.path == "/pass/serviceLogin":
            log("LOGIN step1 serviceLogin sid=%s" % q.get("sid"))
            self._jsonp(200, {"code": 70016, "description": "未登录",
                              "sign": "mock-sign-" + "".join(random.choices(string.ascii_lowercase, k=8))})
            return
        if u.path == "/pass/serviceLoginCallback":
            cookies = self._cookies()
            ok_uid = cookies.get("userId") == MOCK_USERID
            has_sign = bool(q.get("_sign"))
            log("LOGIN step3 callback userId_cookie=%s _sign=%s -> %s"
                % (cookies.get("userId"), has_sign, "ok" if (ok_uid and has_sign) else "reject"))
            if ok_uid and has_sign:
                token = "mock-service-token-" + "".join(random.choices(string.ascii_lowercase + string.digits, k=12))
                self._jsonp(200, {"code": 0, "description": "成功",
                                  "location": "http://%s/sts" % self.headers.get("Host", "127.0.0.1")},
                            set_cookie=["serviceToken=%s; Path=/" % token])
            else:
                self._jsonp(200, {"code": 70016, "description": "会话缺失或签名缺失（需先 serviceLoginAuth2 并携带 _sign）"})
            return
        if u.path == "/mock/state":
            if q.get("admin") != _ADMIN:
                self._send(403, {"error": "bad admin token"})
                return
            self._send(200, json.dumps(_state, ensure_ascii=False, indent=2))
            return
        self._send(404, {"error": "not found", "path": u.path})

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        form = self._form()
        if u.path == "/pass/serviceLoginAuth2":
            user = form.get("user", "")
            h = form.get("hash", "")
            expect = hashlib.sha1(MOCK_PASS.encode()).hexdigest().upper()
            ok = user == MOCK_USER and h == expect
            log("LOGIN step2 serviceLoginAuth2 user=%s hash_ok=%s -> %s" % (user, h == expect, "ok" if ok else "reject"))
            if not ok:
                self._jsonp(200, {"code": 70016, "description": "用户名或密码不正确"})
                return
            ssecurity = base64.b64encode(random.randbytes(32)).decode()
            nonce = base64.b64encode(random.randbytes(12)).decode()[:16]
            host = self.headers.get("Host", "127.0.0.1")
            loc = ("http://%s/pass/serviceLoginCallback?userId=%s&ssecurity=%s&nonce=%s&clientSign=mock"
                   % (host, MOCK_USERID, urllib.parse.quote(ssecurity), nonce))
            self._jsonp(200, {"code": 0, "description": "成功", "location": loc,
                              "userId": MOCK_USERID, "ssecurity": ssecurity},
                        set_cookie=["userId=%s; Path=/" % MOCK_USERID])
            return

        if u.path == "/mock/set_prop":
            if form.get("admin") != _ADMIN:
                self._send(403, {"error": "bad admin token"})
                return
            dev = find_device(form.get("did"))
            if not dev:
                self._send(404, {"error": "no such did"})
                return
            key = "%s.%s" % (form.get("siid"), form.get("piid"))
            if key not in dev["props"]:
                self._send(404, {"error": "no such prop", "prop": key})
                return
            dev["props"][key] = json.loads(form.get("value", "null"))
            save_state()
            log("ADMIN set_prop did=%s %s=%s" % (form.get("did"), key, form.get("value")))
            self._send(200, {"ok": True, "prop": key, "value": dev["props"][key]})
            return

        # ---- 以下均为签名业务接口：必须有 userId+serviceToken cookie 与签名表单字段 ----
        cookies = self._cookies()
        authed = (cookies.get("userId") == MOCK_USERID and str(cookies.get("serviceToken", "")).startswith("mock-service-token-"))
        signed = all(k in form for k in ("signature", "_nonce", "data", "requestId"))
        if not (authed and signed):
            log("API %s REJECT authed=%s signed=%s" % (u.path, authed, signed))
            self._send(401, {"error": "unauthorized",
                             "hint": "需要先完成三步登录获得 userId+serviceToken cookie，且 POST 表单含 signature/_nonce/data/requestId"})
            return

        try:
            data = json.loads(form.get("data", ""))
        except ValueError:
            self._send(400, {"error": "data 不是合法 JSON"})
            return

        if u.path == "/app/home/device_list":
            log("API device_list dids=%s" % form.get("data"))
            devices = [{"did": d["did"], "name": d["name"], "model": d["model"],
                        "isOnline": d["isOnline"], "region": "cn"} for d in _state["devices"]]
            self._send(200, {"code": 0, "result": {"list": devices}})
            return

        if u.path == "/app/home/deviceapi":
            did = data.get("did")
            command = data.get("command")
            try:
                specs = json.loads(data.get("miSpec", "[]"))
            except ValueError:
                specs = data.get("miSpec")
            dev = find_device(did)
            if not dev:
                self._send(200, {"code": -1, "message": "设备不存在: %s" % did})
                return
            if not dev.get("isOnline"):
                log("API deviceapi did=%s cmd=%s OFFLINE" % (did, command))
                self._send(200, {"code": -2, "message": "设备离线"})
                return
            results = []
            for sp in specs or []:
                key = "%s.%s" % (sp.get("siid"), sp.get("piid"))
                known = key in dev["props"]
                if command == "read":
                    if known:
                        results.append({"siid": sp.get("siid"), "piid": sp.get("piid"),
                                        "value": dev["props"][key], "code": 0})
                    else:
                        results.append({"siid": sp.get("siid"), "piid": sp.get("piid"),
                                        "code": -1001, "reason": "unknown property: siid %s piid %s 不存在，可探测其他 siid/piid" % (sp.get("siid"), sp.get("piid"))})
                elif command == "write":
                    if not known:
                        results.append({"siid": sp.get("siid"), "piid": sp.get("piid"),
                                        "code": -1001, "reason": "unknown property"})
                    elif "value" not in sp:
                        results.append({"siid": sp.get("siid"), "piid": sp.get("piid"),
                                        "code": -1003, "reason": "write 需要带 value"})
                    else:
                        t = dev.get("prop_types", {}).get(key)
                        v = sp["value"]
                        bad = (t == "bool" and not isinstance(v, bool)) or (t == "int" and not isinstance(v, int))
                        if bad:
                            results.append({"siid": sp.get("siid"), "piid": sp.get("piid"),
                                            "code": -1002, "reason": "value 类型不符，期望 %s" % t})
                        else:
                            dev["props"][key] = v
                            results.append({"siid": sp.get("siid"), "piid": sp.get("piid"),
                                            "value": v, "code": 0})
                else:
                    results.append({"code": -1004, "reason": "command 必须是 read/write"})
            if command == "write":
                save_state()
            log("API deviceapi did=%s(%s) cmd=%s specs=%s -> %s"
                % (did, dev["name"], command, data.get("miSpec"), json.dumps(results, ensure_ascii=False)))
            self._send(200, {"code": 0, "result": results})
            return

        if u.path == "/home/devicecmd":
            did = data.get("did")
            method = data.get("method")
            params = data.get("params", [])
            dev = find_device(did)
            if not dev:
                self._send(200, {"code": -1, "message": "设备不存在"})
                return
            if method == "get_prop" and dev["model"] == "cuco.plug.cp1md":
                result = [dev["props"]["2.1"], "ON" if dev["props"]["2.1"] else "OFF"]
            elif method == "set_power" and dev["model"] == "cuco.plug.cp1md" and isinstance(params, list) and params:
                dev["props"]["2.1"] = bool(params[0])
                save_state()
                result = "ok"
            else:
                result = {"error": "unknown method for device", "method": method}
            log("API devicecmd did=%s(%s) method=%s params=%s -> %s"
                % (did, dev["name"], method, json.dumps(params, ensure_ascii=False), json.dumps(result, ensure_ascii=False)))
            self._send(200, {"code": 0, "result": result})
            return

        self._send(404, {"error": "not found", "path": u.path})


def main():
    global _LOG, _STATE_PATH, _ADMIN
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=18963)
    ap.add_argument("--state", default="data/mijia_mock/state.json")
    ap.add_argument("--log", default="data/mijia_mock/server.log")
    ap.add_argument("--admin-token", default="adgs-mijia-mock-admin")
    args = ap.parse_args()
    _STATE_PATH = args.state
    _LOG = args.log
    _ADMIN = args.admin_token
    os.makedirs(os.path.dirname(os.path.abspath(_LOG)), exist_ok=True)
    load_state(args.state)
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print("[mijia-mock] listening 127.0.0.1:%d state=%s log=%s devices=%s"
          % (args.port, args.state, args.log,
             ", ".join(d["name"] for d in _state["devices"])), flush=True)
    log("SERVER start port=%d devices=%s" % (args.port, [d["name"] for d in _state["devices"]]))
    srv.serve_forever()


if __name__ == "__main__":
    main()

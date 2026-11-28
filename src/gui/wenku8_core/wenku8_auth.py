# -*- coding: utf-8 -*-
"""文库8会话管理。

会话 Cookie 由账号密码登录或官网浏览器登录成功后写入 DataStore 设置项
wenku8_cookie，只保存在本机；软件不内置任何账号凭据。
所有 network 请求统一经 make_session() 注入，避免散落各处的登录态处理。
"""

from __future__ import annotations

from typing import Any, Optional

import requests

from .wenku8_http import (
    make_fingerprint_session,
    make_requests_session,
    network_mode,
)

# 站点域名候选。2026 年起官方 App API（app.wenku8.com）与老网页接口
# （/modules/article/search.php）已关停；实测 .cc 的网页详情页/目录/正文
# （/book/{id}.htm、/novel/1/{id}/index.htm、/novel/1/{id}/{cid}.htm）仍可用，
# 而 .net/.com 在多地返回 Cloudflare 403 或空响应。故把 .cc 提到首位。
# 用户仍可在设置页用 wenku8_base_url 手动固定任意域名。
_BASE_CANDIDATES = (
    "https://www.wenku8.cc",
    "https://www.wenku8.net",
    "https://www.wenku8.com",
)
# 线路名（用于「官网线路」管理页展示），未列出的自定义线路用域名本身。
_LINE_LABELS = {
    "https://www.wenku8.cc": "wenku8.cc（优先）",
    "https://www.wenku8.net": "wenku8.net",
    "https://www.wenku8.com": "wenku8.com",
}
_BASE_URL = _BASE_CANDIDATES[0]  # 兼容旧引用；实际取域名请用 get_base_url()
# 与 mikufoxxx/Wenku8_Flutter 发行版一致：WebView 走桌面 Edge 指纹，
# Cloudflare 放行 WebView 后，requests 复用同一 UA 才能带 clearance 正常访问。
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36 Edg/143.0.0.0"
)


def get_cookie() -> str:
    """读取文库8会话 Cookie：仅来自本机用户自己登录后的设置项。

    软件不内置任何账号凭据；未登录时返回空字符串。
    会话由账号密码登录或官网浏览器登录成功后写入，只保存在本机。
    """
    try:
        from gui.datastore import DataStore
        return str(DataStore().settings_get("wenku8_cookie", "") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def set_cookie(cookie: str) -> None:
    """保存文库8 Cookie（设置页调用）。"""
    try:
        from gui.datastore import DataStore
        DataStore().settings_set("wenku8_cookie", str(cookie or "").strip())
    except Exception:  # noqa: BLE001
        pass


def parse_cookie(cookie: str) -> dict:
    """把 'a=1; b=2' 形式拆成字典。"""
    out = {}
    for part in str(cookie or "").split(";"):
        key, sep, val = part.strip().partition("=")
        if sep and key:
            out[key] = val
    return out


def get_base_url() -> str:
    """返回当前文库8站点根地址（挑刺 #6：与登录线路统一为单一真相源）。

    优先级：用户固定值 `wenku8_base_url` > 线路管理 active > 探测缓存 >
    候选首项（.cc）。

    关键修复：搜索走本函数、登录/线路走 `get_active_base()`，过去两者读
    不同设置项（wenku8_base_url vs wenku8_lines.active），可长期分叉导致
    「登录后搜索仍打旧域名」。现在把线路 active 纳入本函数的优先级链，
    两处自然指向同一域名；`get_active_base()` 仍以线路 active 为先，二者一致。
    """
    try:
        from gui.datastore import DataStore
        custom = str(DataStore().settings_get("wenku8_base_url", "") or "").strip()
        if custom:
            return custom.rstrip("/")
    except Exception:  # noqa: BLE001
        pass
    # 线路管理中选中的线路是「当前生效域名」的权威来源之一。
    try:
        active = str(get_lines_state().get("active", "") or "").strip()
        if active:
            return active.rstrip("/")
    except Exception:  # noqa: BLE001
        pass
    try:
        from gui.datastore import DataStore
        cached = str(DataStore().settings_get("wenku8_base_cached", "") or "").strip()
        if cached:
            return cached.rstrip("/")
    except Exception:  # noqa: BLE001
        pass
    return _BASE_CANDIDATES[0]


def probe_base_url(session=None, timeout: int = 8) -> str:
    """探测候选域名，返回第一个可正常访问（非关站页/非纯登录跳转）的根地址并缓存。

    探测失败时回退到候选首个，绝不抛异常。桌面端网络正常时应能命中活站。
    """
    sess = session or make_session()
    for base in _BASE_CANDIDATES:
        # 用详情页 /book/1034.htm 判定可用性，比首页更可靠：
        # 首页可能是关站占位或 Cloudflare 403，而详情静态页仍可读。
        for probe_path in ("/book/1034.htm", "/"):
            try:
                # 挑刺 #7：不再显式覆盖 User-Agent。make_session 返回的
                # httpcloak 会话自带与 TLS 指纹一致的 UA；强塞 _UA 反而制造
                # 「UA 说 Edge / TLS 说 Chrome」的指纹矛盾，招致风控。
                resp = sess.get(base + probe_path, timeout=timeout,
                                allow_redirects=True)
                resp.encoding = "gbk"
                text = resp.text or ""
                low = text.lower()
                # 拦截/占位/关站：换下一个探测路径或域名
                if ("Attention Required" in text or "just a moment" in low
                        or "cf-error" in low or "<h1>welcome</h1>" in low
                        or "本站正式关闭" in text or "正式关闭" in text):
                    continue
                # 正常站点：详情页含书名/作者/文库标识之一即视为可用
                if ("轻小说文库" in text or "/book/" in text
                        or "会员中心" in text or "恶魔高校" in text
                        or "书名" in text or "作者" in text):
                    try:
                        from gui.datastore import DataStore
                        DataStore().settings_set("wenku8_base_cached", base)
                    except Exception:  # noqa: BLE001
                        pass
                    return base
            except Exception:  # noqa: BLE001
                continue
    return _BASE_CANDIDATES[0]


def make_session(cookie: Optional[str] = None, timeout: int = 20,
                 base: Optional[str] = None) -> Any:
    """构造带登录态的文库8会话，供搜索/详情/目录/正文统一复用。

    网络层由 `wenku8_http` 决定：装了 httpcloak 就走全栈浏览器指纹通道
    （本机诊断已证明这是唯一能稳定过 Cloudflare 的方案），没装则退回
    requests，保证离线单测与老环境不崩。

    Cookie 的 domain 随站点域名动态调整（.net/.cc/.com），避免旧版写死
    `.wenku8.cc` 导致换域名后登录态失效。

    :param base: 显式指定站点域名。验证用户粘贴的 Cookie 时必须传入 Cookie
        实际来源的域名——否则默认 base 可能是 .net，而 Cookie 来自 .cc，
        域名不匹配导致登录态被判无效。
    """
    base = (base or get_base_url()).rstrip("/")
    raw = cookie if cookie is not None else get_cookie()
    if network_mode() == "httpcloak":
        try:
            return make_fingerprint_session(raw, base, timeout=timeout)
        except Exception:  # noqa: BLE001
            # 指纹会话创建失败时仍退回 requests，不让整个功能不可用
            pass
    return make_requests_session(raw, base, timeout=timeout, user_agent=_UA)


def is_logged_in(session: Optional[requests.Session] = None,
                 base: Optional[str] = None, strict: bool = False) -> bool:
    """探测登录态是否有效。

    判定优先级：
    1. 宽松模式（strict=False，默认）：Session Cookie 含 jieqiUserInfo
       （Jieqi CMS 登录标志）即判有效。这是最可靠的离线判据——不受域名
       匹配 / Cloudflare 风控 / 站点改版文案变化影响。用户从浏览器复制的
       完整 Cookie 含此标志，过去因域名不匹配或风控被误判「无效」。
    2. 网络判定：访问首页，跳 login.php → 无效；含会员标记 → 有效。
       注意文库8首页对游客公开，不能仅凭「没跳登录页 / 有书籍链接」判有效。

    :param strict: True 时跳过 Cookie 标志判据，只认首页会员标记。
        逐线路推断 Cookie 来源域名时用，避免含标志的 Cookie 在每条线路都命中。
    """
    site = (base or get_base_url()).rstrip("/")
    sess = session or make_session(base=site)
    if not strict:
        try:
            if str(sess.cookies.get_dict().get("jieqiUserInfo", "")).strip():
                return True
        except Exception:  # noqa: BLE001
            pass
    try:
        resp = sess.get(site + "/", timeout=15, allow_redirects=True)
        if "login.php" in resp.url.lower():
            return False
        # 站点全站 GBK；显式按 gbk 解码避免 requests 误判 utf-8 导致中文乱码
        resp.encoding = "gbk"
        text = resp.text or ""
        # 登录后首页才出现的会员标记（游客首页没有）
        if any(k in text for k in ("会员中心", "我的书架", "退出登录", "个人中心")):
            return True
        return False
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# 账号密码登录（协议来自抓包分析，交叉验证 guohuiyuan/go-novel-dl 等开源实现）
# POST /login.php?do=submit&jumpurl=...  Body: username/password/usecookie/action/submit
# 成功判定：响应 Cookie 含 jieqiUserInfo（响应可能是挑战页但 Set-Cookie 已生效）
# ---------------------------------------------------------------------------

def _build_login_form(username: str, password: str) -> dict:
    """构造登录 POST 表单（纯函数，便于单测校验协议字段）。"""
    # submit 按钮值是 GBK 编码的「登录」，用 latin-1 保留字节，
    # requests 以 x-www-form-urlencoded 发送时按字节透传。
    submit_gbk = "登录".encode("gbk")
    return {
        "username": str(username or "").strip(),
        "password": str(password or ""),  # 站点明文传输，不做转换
        "usecookie": "315360000",         # 记住登录一年
        "action": "login",
        "submit": submit_gbk,
    }


def login_with_password(username: str, password: str,
                        base: Optional[str] = None) -> tuple[bool, str, str]:
    """用账号密码登录文库8，成功返回 (True, cookie字符串, 提示)。

    失败返回 (False, "", 原因)。登录成功后把新会话 Cookie 序列化为字符串，
    可直接交给 set_cookie() 保存，与现有 Cookie 登录机制无缝复用。

    注意：站点可能被 Cloudflare 风控（403 / 人机验证页），此时如实返回失败，
    不伪造成功；用户可改用 Cookie 登录方式。
    """
    username = str(username or "").strip()
    password = str(password or "")
    if not username or not password:
        return False, "", "账号或密码为空"
    site = (base or get_base_url()).rstrip("/")
    from urllib.parse import quote
    jumpurl = site.replace("https://", "http://", 1) + "/index.php"
    login_url = site + "/login.php?do=submit&jumpurl=" + quote(jumpurl, safe="")
    # 登录也必须走同一套指纹会话：普通 requests 在 .net/.com 必被 403，
    # 即便在 .cc 能打开详情页，POST 登录仍可能被风控拦。
    sess = make_session(base=site, timeout=20)
    # 挑刺 #9：Origin/Referer 与登录请求 URL 同协议。jumpurl 为对齐站点
    # 表单仍用 http（原样提交），但浏览器同源头应跟随实际页面协议（https），
    # 避免跨协议 Origin 被站点或 Cloudflare 判为异常。
    origin = site.rsplit("/", 1)[0] + "/" if "/" in site[8:] else site + "/"
    sess.headers.update({
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": origin,
        "Referer": origin,
    })
    try:
        resp = sess.post(login_url, data=_build_login_form(username, password),
                         timeout=20, allow_redirects=True)
    except Exception as exc:  # noqa: BLE001
        return False, "", f"登录请求失败：{exc}"
    if resp.status_code == 403:
        return False, "", "登录被站点风控拦截（403），请改用官网浏览器登录"
    # 站点为 GBK：必须先显式设编码再取 text，否则中文失败文案会被
    # requests 误判为 ISO-8859-1 而乱码，导致"密码错误/验证码"匹配失败。
    resp.encoding = "gbk"
    # 优先以 Cookie 判定成功（最可靠，响应文本可能是挑战页）
    cookies = sess.cookies.get_dict()
    text = resp.text or ""
    ok = "jieqiUserInfo" in cookies
    if not ok and ("登录成功" in text or "退出登录" in text or username in text):
        ok = True
    if not ok:
        # 常见失败文案
        if any(k in text for k in ("密码错误", "用户名不存在", "验证码", "登录失败")):
            return False, "", "账号或密码错误（或需要验证码），请检查后重试"
        return False, "", "登录未成功（可能是站点风控或页面结构变化），请改用官网浏览器登录"
    # 序列化会话 Cookie 为 'k=v; k=v' 供现有机制保存
    cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
    return True, cookie_str, "登录成功"


# ---------------------------------------------------------------------------
# 官网线路管理（参考 mikufoxxx/Wenku8_Flutter 的「官网线路」设计）
# 每条线路独立：域名 / 登录 Cookie / 检测结果 / 使用中标记。
# 存储：DataStore 设置项 wenku8_lines = JSON {
#   "lines": [{"base", "label", "cookie", "state", "detail"}],
#   "active": "<base>", "keep_login": bool }
# 与旧设置兼容：wenku8_base_url / wenku8_cookie 仍为「当前生效线路」的快捷方式。
# ---------------------------------------------------------------------------

_LINES_KEY = "wenku8_lines"

# 挑刺 #14：线路检测状态的合法取值集中定义，避免散落字符串拼写错误。
LINE_STATE_UNCHECKED = "unchecked"
LINE_STATE_OK = "ok"
LINE_STATE_GUEST = "guest"
LINE_STATE_CLOSED = "closed"
LINE_STATE_BLOCKED = "blocked"
LINE_STATE_ERROR = "error"
LINE_STATES = (
    LINE_STATE_UNCHECKED, LINE_STATE_OK, LINE_STATE_GUEST,
    LINE_STATE_CLOSED, LINE_STATE_BLOCKED, LINE_STATE_ERROR,
)


def _default_lines() -> dict:
    lines = []
    for base in _BASE_CANDIDATES:
        lines.append({
            "base": base,
            "label": _LINE_LABELS.get(base, base.replace("https://www.", "")),
            "cookie": "",      # 每条线路独立 Cookie
            "state": "unchecked",  # unchecked/ok/guest/closed/blocked/error
            "detail": "",
        })
    return {"lines": lines, "active": _BASE_CANDIDATES[0], "keep_login": True}


def get_lines_state() -> dict:
    """读取线路管理状态；损坏/缺失时回退默认并落盘规范化。"""
    import json
    try:
        from gui.datastore import DataStore
        raw = str(DataStore().settings_get(_LINES_KEY, "") or "").strip()
        data = json.loads(raw) if raw else {}
        if not isinstance(data, dict) or not isinstance(data.get("lines"), list):
            raise ValueError("bad lines state")
        # 规范化每条线路字段
        normalized = []
        for item in data["lines"]:
            if not isinstance(item, dict) or not item.get("base"):
                continue
            base = str(item["base"]).strip().rstrip("/")
            normalized.append({
                "base": base,
                "label": str(item.get("label") or _LINE_LABELS.get(base, base)),
                "cookie": str(item.get("cookie") or ""),
                "state": str(item.get("state") or "unchecked"),
                "detail": str(item.get("detail") or ""),
            })
        if not normalized:
            raise ValueError("empty lines")
        data["lines"] = normalized
        data.setdefault("keep_login", True)
        data["active"] = str(data.get("active") or normalized[0]["base"])
        return data
    except Exception:  # noqa: BLE001
        default = _default_lines()
        save_lines_state(default)
        return default


def save_lines_state(data: dict) -> None:
    import json
    try:
        from gui.datastore import DataStore
        DataStore().settings_set(_LINES_KEY, json.dumps(data, ensure_ascii=False))
    except Exception:  # noqa: BLE001
        pass


def get_active_base() -> str:
    """当前使用中线路域名。优先线路状态里的 active，其次旧设置，默认 .net。"""
    try:
        state = get_lines_state()
        if state.get("active"):
            return str(state["active"]).rstrip("/")
    except Exception:  # noqa: BLE001
        pass
    return get_base_url()


def set_active_base(base: str, sync_cookie: bool = True) -> str:
    """切换使用中线路：更新 active + 旧快捷设置。

    返回实际切换到的 base。目标不存在时自动追加为自定义线路。

    :param sync_cookie: 是否把该线路的独立 Cookie 同步为全局生效值。
        线路页切换（正常场景）传 True；设置页仅改域名时应传 False，
        避免把用户手填的 Cookie 误清空或误切换。
    """
    base = str(base or "").strip().rstrip("/")
    state = get_lines_state()
    bases = {ln["base"]: ln for ln in state["lines"]}
    if base not in bases:
        state["lines"].append({
            "base": base, "label": _LINE_LABELS.get(base, base.replace("https://www.", "")),
            "cookie": "", "state": "unchecked", "detail": "",
        })
        bases[base] = state["lines"][-1]
    state["active"] = base
    save_lines_state(state)
    # 同步旧快捷设置，使 get_base_url()/make_session() 立即生效
    try:
        from gui.datastore import DataStore
        DataStore().settings_set("wenku8_base_url", base)
    except Exception:  # noqa: BLE001
        pass
    # 把该线路 Cookie 同步为当前生效 Cookie（无则清空，回退内置）
    if sync_cookie:
        set_cookie(bases[base].get("cookie") or "")
    return base


def save_line_cookie(base: str, cookie: str) -> None:
    """保存某线路的登录 Cookie；若该线路正是使用中线路，同步为当前生效值。"""
    base = str(base or "").strip().rstrip("/")
    state = get_lines_state()
    for ln in state["lines"]:
        if ln["base"] == base:
            ln["cookie"] = str(cookie or "").strip()
            break
    else:
        state["lines"].append({
            "base": base, "label": _LINE_LABELS.get(base, base.replace("https://www.", "")),
            "cookie": str(cookie or "").strip(), "state": "unchecked", "detail": "",
        })
    save_lines_state(state)
    if state.get("active") == base:
        set_cookie(cookie or "")


def add_line(base: str) -> tuple[bool, str, str]:
    """新增自定义线路。返回 (ok, 规范化base, 提示)。"""
    base = str(base or "").strip().rstrip("/")
    if not base:
        return False, "", "线路地址不能为空"
    if not base.startswith(("http://", "https://")):
        base = "https://" + base
    state = get_lines_state()
    if any(ln["base"] == base for ln in state["lines"]):
        return False, base, "该线路已存在"
    state["lines"].append({
        "base": base, "label": base.replace("https://www.", "").replace("https://", ""),
        "cookie": "", "state": "unchecked", "detail": "",
    })
    save_lines_state(state)
    return True, base, "线路已添加"


def remove_line(base: str) -> bool:
    """删除自定义线路；内置候选线路不可删除。返回是否成功。"""
    base = str(base or "").strip().rstrip("/")
    if base in _BASE_CANDIDATES:
        return False
    state = get_lines_state()
    state["lines"] = [ln for ln in state["lines"] if ln["base"] != base]
    if state.get("active") == base:
        # 挑刺 #13：删当前线路不该无声清空全局登录态。切到第一条剩余线路，
        # 并把该线路自己保存的 Cookie 同步为当前生效值；仅当剩余线路都没有
        # 登录态时才清空（此时确实无处可继承）。
        fallback = state["lines"][0] if state["lines"] else None
        new_active = fallback["base"] if fallback else _BASE_CANDIDATES[0]
        state["active"] = new_active
        inherited_cookie = str((fallback or {}).get("cookie", "") or "")
        set_cookie(inherited_cookie)
        try:
            from gui.datastore import DataStore
            DataStore().settings_set("wenku8_base_url", new_active)
        except Exception:  # noqa: BLE001
            pass
    save_lines_state(state)
    return True


def set_line_checked(base: str, state_value: str, detail: str = "") -> None:
    """记录某线路的检测结果（state: ok/guest/closed/blocked/error/unchecked）。"""
    base = str(base or "").strip().rstrip("/")
    data = get_lines_state()
    for ln in data["lines"]:
        if ln["base"] == base:
            ln["state"] = state_value
            ln["detail"] = str(detail or "")
            break
    save_lines_state(data)


def verify_cookie_on_lines(cookie: str, timeout: int = 8) -> tuple[bool, str, str]:
    """验证粘贴的完整 Cookie 是否为有效登录态，返回 (有效, 命中域名, 提示)。

    用户从浏览器复制的 Cookie 只属于某一个域名（如 wenku8.cc），但软件
    默认请求域名可能是另一个（如 .net）。过去把 Cookie 拿到错误域名验证，
    站点不认 → 跳登录页 → 误判「登录态无效」。这里改为两层判定：

    1. 逐线路严格探测（strict=True）：只认首页会员标记，用于推断 Cookie
       真正来源域名；命中后设为使用中线路，下载立即用正确域名。
    2. Cookie 标志兜底：Cookie 含 jieqiUserInfo 即确认登录态有效。即使
       Cloudflare 风控导致所有线路探测失败，也不再把完整 Cookie 误判无效；
       此时沿用当前使用中线路并如实提示「网络探测受限」。

    不改 Cookie 值本身；网络异常不抛异常。
    """
    cookie = str(cookie or "").replace("\r", "").replace("\n", ";").strip()
    if not cookie:
        return False, "", "Cookie 为空"

    # 登录态权威标志：Jieqi CMS 登录后写入，未登录时不存在
    parsed = parse_cookie(cookie)
    has_marker = bool(str(parsed.get("jieqiUserInfo", "")).strip())

    state = get_lines_state()
    bases = [ln["base"] for ln in state["lines"]]
    # 兜底：状态里没有候选时补默认三条
    for b in _BASE_CANDIDATES:
        if b not in bases:
            bases.append(b)

    # 第一层：逐线路严格探测，推断来源域名。
    # 注意：某线路跳 login.php 只说明「这份 Cookie 不属于该域名」（如拿 .cc
    # 的 Cookie 去 .net 验证），并不代表 Cookie 本身无效——正确域名还没试完，
    # 因此这里不因个别线路的未登录响应而提前否定。
    for base in bases:
        try:
            sess = make_session(cookie, timeout=timeout, base=base)
            ok = is_logged_in(sess, base=base, strict=True)
        except Exception:  # noqa: BLE001
            ok = False
        if ok:
            # 命中域名 → 设为使用中线路，并同步 Cookie 到该线路
            try:
                set_active_base(base)
                save_line_cookie(base, cookie)
                # 命中即清除之前保存的「未登录」误标记，避免卡片矛盾
                state = get_lines_state()
                for ln in state["lines"]:
                    if ln["base"] == base:
                        ln["state"] = "ok"
                        ln["detail"] = "登录态有效（Cookie 验证命中）"
                save_lines_state(state)
            except Exception:  # noqa: BLE001
                pass
            return True, base, f"登录态有效（线路：{base}）"

    # 第二层：所有线路的严格探测都没命中（Cloudflare 风控 / 域名错配 / 站点
    # 改版）。此时 Cookie 里的 jieqiUserInfo 是浏览器登录后写入的权威标志，
    # 用户从 F12 复制的完整 Cookie 带此标志即视为有效，不因网络探测失败误判。
    if has_marker:
        active = get_active_base()
        try:
            save_line_cookie(active, cookie)
        except Exception:  # noqa: BLE001
            pass
        return True, active, "登录态有效（依据 Cookie 登录标志；网络探测受限）"

    return False, "", "Cookie 中未找到登录标志（jieqiUserInfo），且所有线路均未识别到登录态，请重新获取完整 Cookie"


def classify_line_response(status: int, final_url: str, text: str) -> tuple[str, str]:
    """纯函数：把一次线路探测响应归类为 (state, detail)，便于单测。

    state: ok(已登录/可用) / guest(可访问未登录) / closed(关站) /
           blocked(风控拦截) / error(其它异常)
    """
    url = str(final_url or "")
    body = str(text or "")
    if status == 403 or "人机验证" in body or "cloudflare" in body.lower():
        return LINE_STATE_BLOCKED, "站点风控拦截（403/人机验证）"
    if "本站正式关闭" in body or "正式关闭" in body:
        return LINE_STATE_CLOSED, "站点已关闭（关站旧页）"
    if "login.php" in url.lower():
        return LINE_STATE_GUEST, "可访问，但未登录（跳转登录页）"
    if any(k in body for k in ("会员中心", "我的书架", "退出登录")):
        return LINE_STATE_OK, "可访问，登录态有效"
    if "轻小说文库" in body or "/book/" in body or "小说排行榜" in body:
        return LINE_STATE_GUEST, "可访问，未识别到登录态"
    return LINE_STATE_GUEST, "线路可访问（页面结构未知）"


def check_line(base: str, cookie: str = "", timeout: int = 8) -> tuple[str, str]:
    """检测单条线路连接状态，返回 (state, detail)，不抛异常、不写数据。"""
    base = str(base or "").strip().rstrip("/")
    # 线路检测同样不能再用裸 requests：诊断显示裸通道全线 403，会把
    # 可用线路误标记为「风控拦截」。统一走 make_session 的指纹通道。
    sess = make_session(cookie, timeout=timeout, base=base)
    try:
        resp = sess.get(base + "/", timeout=timeout, allow_redirects=True)
        # 必须先按 GBK 解码再取 text：requests 默认按 apparent_encoding 猜测，
        # GBK 页面常被误判为 ISO-8859-1 导致中文乱码 → classify 的中文关键字
        # （会员中心/本站正式关闭）全部匹配失败 → 线路状态误判为 guest。
        resp.encoding = "gbk"
        text = resp.text or ""
        state, detail = classify_line_response(resp.status_code, resp.url, text)
    except Exception as exc:  # noqa: BLE001
        state, detail = "error", f"连接失败：{exc}"

    # 风控误报修正：requests 用脚本指纹请求，Cloudflare 对其返回 403 是常态，
    # 但这不代表线路在浏览器里不可用。若该线路已保存有效登录 Cookie（用户
    # 走官网浏览器登录成功才拿得到 jieqiUserInfo），说明浏览器上下文可正常
    # 访问，此时不应把线路标记为「风控拦截」误导用户。
    if state == "blocked":
        try:
            from .wenku8_web_context import has_login_marker
            has_login = has_login_marker(cookie)
        except Exception:  # noqa: BLE001
            has_login = False
        if has_login:
            state = "ok"
            detail = ("已登录（官网浏览器通道可用）；脚本探测被风控拦截，"
                      "不影响官网登录与浏览器搜索")

    set_line_checked(base, state, detail)
    return state, detail

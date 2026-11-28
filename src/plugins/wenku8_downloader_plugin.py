# -*- coding: utf-8 -*-
"""文库8（轻小说文库）真实下载内核插件。

内核为本项目自研（src/gui/wenku8_core/），不依赖未开源的上游实现。
本文件是插件薄适配层：把核心的「书籍信息 / 目录 / 正文」包装成
本项目的 BookSource，再交给 qimao_core.build_txt 复用全部导出能力
（TXT / 分章 / EPUB、章节范围、文件名模板、题图尾图、字体颜色、对话着色）。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gui.plugin.base import (
    Plugin, PluginMeta, DownloaderBase, DownloadResult, DownloadCancelled,
)
from gui.qimao_core import BookSource, build_txt, sanitize_filename


class Wenku8BookSource(BookSource):
    """文库8章节源：自研内核，逐章明文抓取（无字体混淆）。"""

    source_key = "wenku8"
    name = "文库8"

    def __init__(self) -> None:
        self._total = 0
        self._prefix = ""
        self._aid = ""
        self._app_mode = False
        # 分卷导出缓存：全书详情/目录/正文只抓一次，多卷复用（正文抓取是整链路瓶颈）。
        # key 用 str(book_id) / chapter id；只缓存成功结果，失败章不缓存以便重试。
        # 整本下载（默认路径）也会写入缓存，但行为与旧版完全一致，零回归。
        self._detail_cache: dict = {}
        self._catalog_cache: dict = {}
        self._text_cache: dict = {}
        # 插图缓存：key=图片 URL，value=图片字节。预取阶段并发下载，渲染阶段只读。
        self._image_cache: dict = {}
        # 插图严格模式：预取阶段统一下载 + 多轮补抓；最终仍失败的 URL 记在
        # _image_missing，由下载入口判为失败，绝不静默丢图后照常出书。
        self._image_missing: list = []
        # 正确模型：每个工作线程一个会话并长期复用。
        # 取证：
        #   - 每章新建会话：295 章 49 次 unexpected EOF（TLS 握手太频繁）；
        #   - 每线程一个会话：295 章仅 1 次 EOF，重试即可补齐，8.7 秒完成；
        #   - 全书单会话跨线程：httpcloak Session 非线程安全，Windows 原生库
        #     会出现请求互锁/卡顿（用户实测卡在 49%），绝不能这么做。
        import threading
        self._tls = threading.local()
        self._session_lock = threading.Lock()

    def _session(self, session=None):
        """每个线程复用自己的指纹会话；既不每章重建，也不跨线程共享。

        调用方传入的 Session 只有在属于当前线程时才直接复用；跨线程传入时
        只提取 Cookie，由本线程新建会话，避免把非线程安全对象交给多线程。
        """
        import threading
        from gui.wenku8_core.wenku8_auth import make_session, get_cookie
        cookie = get_cookie()
        if session is not None and getattr(session, "cookies", None) is not None:
            try:
                jar = session.cookies.get_dict()
                if "PHPSESSID" in jar or "jieqiUserInfo" in jar:
                    owner = getattr(session, "owner_thread", None)
                    if owner is None or owner == threading.get_ident():
                        return session
                    cookie = "; ".join(f"{k}={v}" for k, v in jar.items())
            except Exception:  # noqa: BLE001
                pass

        cached = getattr(self._tls, "session", None)
        cached_key = getattr(self._tls, "cookie_key", None)
        if cached is not None and cached_key == cookie:
            return cached
        with self._session_lock:
            cached = getattr(self._tls, "session", None)
            cached_key = getattr(self._tls, "cookie_key", None)
            if cached is not None and cached_key == cookie:
                return cached
            new_session = make_session(cookie)
            self._tls.session = new_session
            self._tls.cookie_key = cookie
            return new_session

    def fetch_detail(self, book_id: str, session) -> dict:
        key = str(book_id)
        cached = self._detail_cache.get(key)
        if cached is not None:
            return cached
        from gui.wenku8_core.wenku8_reader import fetch_detail
        sess = self._session(session)
        d = fetch_detail(book_id, sess)
        result = {
            "title": str(d.get("title") or ""),
            "author": str(d.get("author") or ""),
            "intro": str(d.get("intro") or ""),
            "cover": str(d.get("cover") or ""),
        }
        self._detail_cache[key] = result
        return result

    def fetch_catalog(self, book_id: str, session) -> list:
        key = str(book_id)
        cached = self._catalog_cache.get(key)
        if cached is not None:
            self._total = len(cached)
            return cached
        from gui.wenku8_core.wenku8_reader import (
            resolve_book_id, fetch_catalog, fetch_book_meta,
        )
        sess = self._session(session)
        self._aid = resolve_book_id(book_id) or str(book_id or "").strip()
        raw = fetch_catalog(book_id, sess)
        # App API 目录自带 app_cid/vol，走 do=text 正文，无需网页 novel 前缀
        self._app_mode = any(str(ch.get("app_cid") or "").isdigit() for ch in raw)
        self._prefix = ""
        if not self._app_mode:
            # 仅网页目录通道可用时才需要前缀（兼容旧线路）
            meta = fetch_book_meta(book_id, sess)
            self._prefix = meta.get("novel_prefix", "")
        # 归一为「卷/集/章」层级：title 精简为纯章名，vol/sub 记录分组。
        from gui.hierarchy import normalize_catalog_safe
        raw = normalize_catalog_safe(raw)
        self._total = len(raw)
        self._catalog_cache[key] = raw
        return raw

    def prefetch_book(self, book_id: str, session, on_progress=None):
        """全书预取一次，正文写入缓存，供多卷导出复用。

        返回 (detail, catalog, texts)；texts 与 catalog 一一对应。
        分卷导出时先调本方法，再对每卷 build_txt —— 后续抓取全部命中缓存，
        全书正文只走一次网络（并发 + 多轮补齐），不会重复请求。
        """
        detail = self.fetch_detail(book_id, session)
        catalog = self.fetch_catalog(book_id, session)
        texts = self.prefetch_texts(None, catalog, session, on_progress)
        return detail, catalog, texts

    def _chapter_cache_key(self, chapter: dict) -> str:
        """正文缓存 key：优先章节数字 id，其次 url，最后 index。"""
        return (str(chapter.get("id") or "") or str(chapter.get("url") or "")
                or str(chapter.get("index", "")))

    def get_image(self, url: str) -> bytes:
        """渲染阶段读取插图字节（只读缓存，绝不发网络请求）。"""
        return self._image_cache.get(str(url or ""), b"")

    def image_urls_in(self, texts) -> list:
        """汇总给定正文列表里的全部插图 URL（去重、保序）。"""
        from gui.wenku8_core.wenku8_volumes import extract_image_urls
        seen, out = set(), []
        for t in texts or []:
            for u in extract_image_urls(t):
                if u and u not in seen:
                    seen.add(u)
                    out.append(u)
        return out

    def missing_images(self, texts=None) -> list:
        """返回仍未下载成功的插图 URL。

        texts 给了就以「这些正文里出现的图」为准做校验；否则用上次记录。
        严格模式：这个列表非空 = 插图没下全 = 不允许出书。
        """
        if texts is None:
            return list(self._image_missing)
        wanted = self.image_urls_in(texts)
        return [u for u in wanted if not self._image_cache.get(u)]

    def volume_cover(self, volume_chapters, texts) -> bytes:
        """挑该卷插图章里的第一张竖图作为卷封面字节（仅文库8分卷用）。

        volume_chapters: group_volumes() 里某卷的 chapters（每个带 index）；
        texts: 全书正文列表，按 chapter['index'] 取对应正文。图片字节来自预取
        缓存，不发网络。没有竖图时退回插图章第一张成功的图，保证每卷封面
        来自本卷；整卷插图都失败才返回 b''，由 build_txt 回退详情页总封面。
        """
        from gui.wenku8_core.wenku8_volumes import (
            extract_image_urls, pick_cover_bytes, download_image,
        )
        urls: list = []
        seen = set()
        for ch in volume_chapters or []:
            idx = int(ch.get("index", 0) or 0)
            if idx < 0 or idx >= len(texts or []):
                continue
            # 只看插图章；正文章里的偶发图不作为卷封面
            if not ch.get("is_illustration"):
                continue
            for u in extract_image_urls(texts[idx]):
                if u and u not in seen:
                    seen.add(u)
                    urls.append(u)

        # 卷封面概率性失败修复（全书通用）：预取并发下载时，真封面那张图可能
        # 偶发下载失败（网络抖动/图床限流）。旧逻辑只从缓存挑，缺了就跳过→
        # 降级成目录页/内页竖图，用户看到「封面抓取失败但书成功」。这里给
        # 每个候选一次直达重试机会：缺图就现抓，成功即写回缓存（后续渲染/
        # 严格校验都能看到），失败才降级。图都在缓存时零额外网络请求。
        def _retry(url: str) -> bytes:
            data = download_image(url, attempts=3)
            if data:
                self._image_cache[url] = data
                # 该图此前若在缺图清单里，重试拿回后移除，避免误判整单失败。
                if url in self._image_missing:
                    self._image_missing.remove(url)
            return data

        return pick_cover_bytes(urls, self.get_image, retry=_retry)

    def _cache_images(self, text: str, rounds: int = 4) -> None:
        """抓到正文后，把其中的插图 URL 并发下载进缓存。

        严格实现：多轮补抓——第一轮没拿到的图再按退避重试，直到所有图都
        下载成功或用尽轮次。仍失败的 URL 记入 _image_missing，由下载入口
        判失败，绝不静默丢弃。
        """
        from gui.wenku8_core.wenku8_volumes import (
            extract_image_urls, download_image,
        )
        urls = [u for u in extract_image_urls(text) if u not in self._image_cache]
        if not urls:
            return
        from concurrent.futures import ThreadPoolExecutor
        import time as _time
        pending = list(urls)
        for rnd in range(max(1, int(rounds))):
            if not pending:
                break
            with ThreadPoolExecutor(max_workers=min(8, len(pending))) as pool:
                fmap = {pool.submit(download_image, u): u for u in pending}
                for fut, u in fmap.items():
                    try:
                        data = fut.result()
                    except Exception:  # noqa: BLE001
                        data = b""
                    if data:
                        self._image_cache[u] = data
            pending = [u for u in pending if u not in self._image_cache]
            if pending and rnd < rounds - 1:
                _time.sleep(0.5 * (rnd + 1))
        # 记录最终仍失败的图（并集，不覆盖之前其他章节的失败）
        if pending:
            known = set(self._image_missing)
            for u in pending:
                if u not in known:
                    self._image_missing.append(u)
                    known.add(u)

    def _download_all_images(self, texts, rounds: int = 4,
                             workers: int = 16) -> None:
        """全书插图统一并发下载（与正文通道解耦）。

        图床与正文站不同主机、基本不限流，用独立线程池高并发下载；全书
        去重后只下一次，不在每章任务里串行重试，避免慢图拖住正文预取。
        用 as_completed 收集，单张慢图不会让已完成的图白等。

        严格语义不变：仍失败的 URL 记入 _image_missing，由下载入口判失败，
        绝不静默出缺图的书；卷封面从本缓存取，故必须在渲染前完成。
        """
        import time as _time
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from gui.wenku8_core.wenku8_volumes import download_image

        pending = self.missing_images(texts)
        for rnd in range(max(1, int(rounds))):
            if not pending:
                break
            if rnd:
                _time.sleep(0.3 * rnd)  # 短退避，避免瞬时打爆图床
            with ThreadPoolExecutor(
                    max_workers=max(2, min(workers, len(pending)))) as pool:
                fmap = {pool.submit(download_image, u): u for u in pending}
                for fut in as_completed(fmap):
                    u = fmap[fut]
                    try:
                        data = fut.result() or b""
                    except Exception:  # noqa: BLE001
                        data = b""
                    if data:
                        self._image_cache[u] = data
            pending = [u for u in pending if u not in self._image_cache]

        self._image_missing = [u for u in self.image_urls_in(texts)
                               if u not in self._image_cache]

    def _chapter_text_only(self, chapter: dict, session) -> str:
        """只抓正文，不碰插图——全书并发预取的快通道。

        正文站与图床是不同主机、限流策略完全不同：正文站限流紧、图床基本
        不限流。把插图下载塞在每章抓取任务里，会让正文 worker 卡在图床上，
        且同一张图被多个章节重复下载。这里只取正文，图片由 prefetch_texts
        在全书抓完后统一并发下载一次（见 _download_all_images）。
        """
        ckey = self._chapter_cache_key(chapter)
        cached = self._text_cache.get(ckey)
        if cached:
            return cached
        from gui.wenku8_core.wenku8_reader import (
            fetch_chapter_text, fetch_chapter_text_app,
        )
        sess = self._session(session)
        text = ""
        cid = str(chapter.get("app_cid") or "") if self._app_mode else ""
        if cid:
            text = fetch_chapter_text_app(self._aid, cid, sess)
        if not text:
            # App 正文失败时回退网页通道（若网页可用），不丢章
            url = chapter.get("url") or ""
            text = fetch_chapter_text(
                url or chapter["id"], sess,
                novel_prefix=self._prefix,
                book_id=str(chapter.get("bid", "")),
            )
        if text and ckey:
            self._text_cache[ckey] = text
        return text

    def chapter_text(self, ctx, chapter: dict, session, report) -> str:
        """单章正文 + 插图。追更等单章路径用；整本预取走解耦快通道。"""
        text = self._chapter_text_only(chapter, session)
        if text:
            self._cache_images(text)
        if self._total and report is not None:
            idx = int(chapter.get("index", 0)) + 1
            report(2 + min(38, int(idx / self._total * 38)))
        return text

    def prefetch_texts(self, ctx, catalog, session, on_progress=None):
        """并发预取全书正文（文库8逐章请求，20 章小书可直接并发）。"""
        import time as _time
        from concurrent.futures import ThreadPoolExecutor, as_completed
        try:
            from gui.datastore import DataStore
            workers = int(DataStore().settings_get("wenku8_concurrent_workers", 8) or 8)
        except Exception:  # noqa: BLE001
            workers = 8
        workers = max(1, min(16, workers))
        total = len(catalog)
        if total <= 1:
            texts = [self._chapter_text_only(ch, session) for ch in catalog]
            self._download_all_images(texts)
            return texts

        from gui.wenku8_core.wenku8_volumes import (
            download_image, extract_image_urls,
        )
        # 图片池与正文抓取重叠：正文 worker 只管取文，发现图片就非阻塞提交
        # 到独立图池；图床不限流，16 并发实测最优（32 反而被降速）。
        # _grab 在主线程收集结果，故 img_futs/img_seen 无需加锁。
        img_pool = ThreadPoolExecutor(max_workers=16)
        img_futs: dict = {}
        img_seen = set()

        def _submit_images(text):
            for u in extract_image_urls(text):
                if u in img_seen or u in self._image_cache:
                    continue
                img_seen.add(u)
                img_futs[img_pool.submit(download_image, u)] = u

        state = {"done": 0}

        def _grab(indices, pool):
            got = {}
            # 只抓正文；插图不在这里下载，避免正文 worker 被图床拖住。
            fmap = {
                pool.submit(self._chapter_text_only, catalog[i], session): i
                for i in indices
            }
            for fut in as_completed(fmap):
                idx = fmap[fut]
                try:
                    got[idx] = fut.result() or ""
                except Exception:  # noqa: BLE001
                    got[idx] = ""
                if got[idx]:
                    _submit_images(got[idx])  # 非阻塞提交，与正文抓取重叠
                state["done"] += 1
                if on_progress is not None:
                    on_progress(min(state["done"], total), total)
            return got

        results = {}
        pool = ThreadPoolExecutor(max_workers=workers)
        try:
            results.update(_grab(range(total), pool))
        except BaseException:
            pool.shutdown(wait=False, cancel_futures=True)
            img_pool.shutdown(wait=False, cancel_futures=True)
            raise
        else:
            pool.shutdown(wait=True)

        # 全量补齐留在并发阶段：渲染循环里逐章串行重试会让单章最坏等待数百秒，
        # 进度卡在 40%。但补抓过猛会触发站点限流（用户实测 34/70 章失败、
        # 耗时 263s）。改为「少轮次 + 指数退避 + 递减并发」：
        #   - 单章内部已有 4 次指数退避重试（_get_with_retry），外层不再叠 5 轮；
        #   - 轮间 1s→2s→4s，给站点限流窗口恢复时间；
        #   - 补抓并发逐轮减半，第一轮失败多说明被限流，主动降速而非加压。
        failed = [i for i in range(total) if not results.get(i)]
        for rescue_round in range(1, 4):
            if not failed:
                break
            _time.sleep(min(4.0, 2 ** (rescue_round - 1)))
            rescue_workers = max(2, workers // (2 ** rescue_round))
            pool2 = ThreadPoolExecutor(max_workers=rescue_workers)
            try:
                retried = _grab(failed, pool2)
            except BaseException:
                pool2.shutdown(wait=False, cancel_futures=True)
                img_pool.shutdown(wait=False, cancel_futures=True)
                raise
            else:
                pool2.shutdown(wait=True)
            for i, txt in retried.items():
                if txt:
                    results[i] = txt
            failed = [i for i in failed if not results.get(i)]

        # 正文阶段结束，收割与它重叠跑完的插图（按完成顺序写缓存，不等慢图）
        for fut in as_completed(img_futs):
            u = img_futs[fut]
            try:
                data = fut.result() or b""
            except Exception:  # noqa: BLE001
                data = b""
            if data:
                self._image_cache[u] = data
        img_pool.shutdown(wait=True)

        texts = [results.get(i, "") for i in range(total)]
        # 插图在正文全部抓完后统一并发下载：图床与正文站不同主机、不限流，
        # 单独线程池高并发跑，去重后全书只下一次；严格模式与卷封面不变。
        self._download_all_images(texts)
        return texts


class Wenku8Downloader(DownloaderBase):
    """文库8真实下载内核。"""

    name = "文库8真实内核（自研）"
    source_key = "wenku8"
    priority = 50

    def supports(self, book_info):
        return str((book_info or {}).get("source", "") or "") == "wenku8"

    def _download_volumes(self, book_id, save_path, fmt, progress_cb,
                          kwargs, mode, selected_volumes):
        """按卷导出（文库8专属）：全书正文只抓一次，按卷切片写出多个文件。

        - 先 prefetch_book() 一次性并发抓全书详情/目录/正文并写入书源缓存；
        - 再按目录 vol 分组，对每卷调用 build_txt(start/end) 渲染单卷文件；
          此时详情/目录/正文全部命中缓存，不会重复发任何网络请求；
        - 进度：预取 2→40，N 卷平分 40→99，全部成功后统一 100。
        """
        import requests
        from gui.qimao_core import build_txt as _build_txt
        from gui.datastore import DataStore
        from gui.wenku8_core.wenku8_volumes import (
            group_volumes, select_volumes, volume_filename_template,
            volume_label,
        )

        title = kwargs.get("title", "") or ""
        store = DataStore()

        def opt(key, default=None):
            return kwargs.get(key) or store.settings_get(f"epub_{key}", default)

        chapter_font = opt("chapter_font", "builtin:mashan") or "builtin:mashan"
        volume_font = opt("volume_font", "builtin:zhimang") or "builtin:zhimang"
        chapter_color = opt("chapter_color", "") or ""
        volume_color = opt("volume_color", "") or ""
        colorize = kwargs.get("colorize_dialogue")
        if colorize is None:
            colorize = bool(store.settings_get("epub_colorize_dialogue", True))
        chapter_hero = kwargs.get("chapter_hero")
        if chapter_hero is None:
            chapter_hero = bool(store.settings_get("epub_chapter_hero", False))
        hero_mode = opt("hero_mode", "rotate") or "rotate"
        hero_fixed = opt("hero_fixed", "") or ""
        chapter_tail = kwargs.get("chapter_tail")
        if chapter_tail is None:
            chapter_tail = bool(store.settings_get("epub_chapter_tail", False))
        tail_mode = opt("tail_mode", "rotate") or "rotate"
        tail_fixed = opt("tail_fixed", "") or ""
        smart_art = kwargs.get("smart_art")
        if smart_art is None:
            smart_art = bool(store.settings_get("epub_smart_art", True))

        source = Wenku8BookSource()
        session = requests.Session()
        base_tpl = kwargs.get("filename_template", "") or ""

        def _prefetch_progress(done, total):
            if progress_cb is not None and total:
                progress_cb(min(40, 2 + int(done / total * 38)))

        try:
            detail, catalog, _texts = source.prefetch_book(
                book_id, session, on_progress=_prefetch_progress)
        except DownloadCancelled:
            raise
        except Exception as exc:  # noqa: BLE001
            return DownloadResult(
                book_id=str(book_id), title=title or f"book_{book_id}",
                success=False, message=f"文库8分卷导出失败（预取正文）: {exc}",
            )

        volumes = group_volumes(catalog)
        chosen = select_volumes(volumes, mode, selected_volumes)
        if not chosen:
            return DownloadResult(
                book_id=str(book_id), title=title or f"book_{book_id}",
                success=False,
                message="文库8分卷导出失败：未选择任何卷（请至少勾选一卷）",
            )

        # 严格模式：图和书必须一起成功。预取阶段已多轮尽力下载；只要本次要
        # 导出的卷里还有图没下下来，就判失败，绝不静默出「没图/缺图」的书。
        # 关键：只校验「所选卷」范围内的图——仅导出第2卷时不该因未导出的第5卷
        # 有张图没下下来而整单失败（那张图根本不会进本单任何一卷 EPUB）。
        scope_texts = []
        for _vol in chosen:
            for _ch in _vol.get("chapters", []):
                _idx = int(_ch.get("index", 0) or 0)
                if 0 <= _idx < len(_texts):
                    scope_texts.append(_texts[_idx])
        missing_imgs = source.missing_images(scope_texts)
        if missing_imgs:
            return DownloadResult(
                book_id=str(book_id), title=title or f"book_{book_id}",
                success=False,
                message=(f"文库8下载失败：本次要导出的卷里有 {len(missing_imgs)} 张"
                         f"插图未能下载，图和书必须一起下载成功，本次不出书。"
                         f"示例：{missing_imgs[0][:120]}"),
                extra={"images_failed": len(missing_imgs)},
            )

        # 分卷产物统一收进「书名/」子目录，而不是平铺在保存目录根部。
        # 只对文库8分卷分支生效——整本下载与其他书源不经过这里。
        folder_name = sanitize_filename(
            title or str(detail.get("title") or "") or f"book_{book_id}"
        ) or f"book_{book_id}"
        volume_root = os.path.join(save_path, folder_name)
        created_root = not os.path.isdir(volume_root)
        try:
            os.makedirs(volume_root, exist_ok=True)
        except OSError as exc:
            return DownloadResult(
                book_id=str(book_id), title=title or f"book_{book_id}",
                success=False,
                message=f"文库8分卷导出失败：无法创建分卷文件夹「{folder_name}」：{exc}",
            )

        n = len(chosen)
        span = 59.0 / n  # 40 → 99
        written: list = []
        failed: list = []
        # 每卷实时进度明细（供任务队列的可收缩卡片逐卷显示）。全书通用，
        # 与具体书名/卷名无关；失败卷在 except 分支单独标红。
        vol_state: list = [{
            "name": str(v.get("name") or f"第{i + 1}卷"),
            "chapters": int(v.get("count", 0) or 0),
            "progress": 0,
            "status": "waiting",
        } for i, v in enumerate(chosen)]

        def _report(percent: int) -> None:
            """上报总进度 + 每卷明细；兼容只接受单参数的旧 progress_cb。"""
            if progress_cb is None:
                return
            try:
                progress_cb(percent, volumes=vol_state)
            except TypeError:  # 旧回调签名 progress_cb(p)
                progress_cb(percent)

        for i, vol in enumerate(chosen):

            def _vol_progress(_p, _base=40 + i * span, _span=span, _i=i):
                pct = max(0, min(100, int(_p)))
                vol_state[_i]["progress"] = pct
                vol_state[_i]["status"] = "downloading" if pct < 100 else "done"
                _report(min(99, int(_base + (pct / 100.0) * _span)))

            # 标记本卷开始
            vol_state[i]["status"] = "downloading"
            vol_state[i]["progress"] = 0
            _report(min(99, int(40 + i * span)))

            try:
                # 卷封面：取该卷插图章里的第一张竖图（无则回退详情页总封面）。
                cover_bytes = source.volume_cover(vol.get("chapters", []), _texts)
                result = _build_txt(
                    book_id=str(book_id),
                    save_path=volume_root,
                    fmt=fmt,
                    progress_cb=_vol_progress,
                    title_hint=title,
                    author_hint=kwargs.get("author", "") or "",
                    start_chapter=int(vol["start"]),
                    end_chapter=int(vol["end"]),
                    filename_template=volume_filename_template(base_tpl, vol["name"]),
                    cover_override=cover_bytes,
                    colorize_dialogue=bool(colorize),
                    chapter_font=str(chapter_font or ""),
                    volume_font=str(volume_font or ""),
                    chapter_color=str(chapter_color or ""),
                    volume_color=str(volume_color or ""),
                    chapter_hero=bool(chapter_hero),
                    hero_mode=str(hero_mode),
                    hero_fixed=str(hero_fixed),
                    hero_library="genshin",
                    chapter_tail=bool(chapter_tail),
                    tail_mode=str(tail_mode),
                    tail_fixed=str(tail_fixed),
                    smart_art=bool(smart_art),
                    source=source,
                )
                written.append({
                    "name": vol["name"],
                    "start": vol["start"],
                    "end": vol["end"],
                    "count": vol["count"],
                    "file_path": result.get("file_path", ""),
                    "chapters": result.get("chapters", 0),
                    "label": volume_label(vol),
                })
                vol_state[i]["progress"] = 100
                vol_state[i]["status"] = "done"
                _report(min(99, int(40 + (i + 1) * span)))
            except DownloadCancelled:
                raise
            except Exception as exc:  # noqa: BLE001
                failed.append(f"{volume_label(vol)}: {exc}")
                vol_state[i]["status"] = "failed"
                vol_state[i]["error"] = str(exc)
                _report(min(99, int(40 + (i + 1) * span)))

        # 严格模式：图和书必须一起成功。任一卷失败（含渲染阶段缺图抛错）即整单
        # 失败，绝不允许「部分卷成功、部分卷缺图」却返回 success=True。同时清理
        # 已经写出的半成品，避免留下缺图/缺卷的残缺 EPUB 误导用户。
        if failed:
            for _w in written:
                _p = _w.get("file_path", "")
                if not _p:
                    continue
                try:
                    if os.path.isdir(_p):
                        import shutil as _shutil
                        _shutil.rmtree(_p, ignore_errors=True)
                    elif os.path.exists(_p):
                        os.remove(_p)
                except Exception:  # noqa: BLE001
                    pass
            # 文件夹是本次新建的 → 整个清掉（含插图等残留）；原本就存在 →
            # 只删空文件夹，绝不碰用户此前放在里面的其它文件。
            try:
                if created_root:
                    import shutil as _shutil
                    _shutil.rmtree(volume_root, ignore_errors=True)
                elif os.path.isdir(volume_root) and not os.listdir(volume_root):
                    os.rmdir(volume_root)
            except Exception:  # noqa: BLE001
                pass
            return DownloadResult(
                book_id=str(book_id), title=title or f"book_{book_id}",
                success=False,
                message=("文库8下载失败：图和书必须一起成功，本次不出书。"
                         f"共 {len(failed)} 卷未成功（" + "；".join(failed[:3]) + "）"),
                extra={"volume_failures": failed,
                       "volumes_written_discarded": len(written),
                       "volumes_progress": vol_state},
            )
        if not written:
            return DownloadResult(
                book_id=str(book_id), title=title or f"book_{book_id}",
                success=False,
                message="文库8分卷导出失败：所有卷均未写出",
            )
        if progress_cb is not None:
            progress_cb(100)

        total_chapters = sum(int(v.get("count", 0)) for v in written)
        fmt_label = {"txt": "TXT", "chapter": "分章TXT", "epub": "EPUB"}.get(fmt, fmt)
        msg = (f"已按卷导出 {len(written)} 卷，共 {total_chapters} 章"
               f"（{fmt_label}·文库8，含全部插图）→ 文件夹：{folder_name}")
        return DownloadResult(
            book_id=str(book_id),
            title=title or str(detail.get("title") or f"book_{book_id}"),
            success=True,
            file_path=written[0].get("file_path", ""),
            message=msg,
            extra={
                "chapters": total_chapters,
                "total": len(catalog),
                "format": fmt,
                "real": True,
                "source": "wenku8",
                "volume_mode": mode,
                "output_dir": volume_root,
                "volumes": written,
                "volumes_progress": vol_state,  # 每卷最终状态，供队列卡片逐卷显示
                "volume_failures": [],   # 走到这里 failed 必为空（非空已在上方判失败）
                "images_failed": 0,       # 走到这里缺图必为空（非空已在上方判失败）
                "author": str(detail.get("author") or kwargs.get("author", "") or ""),
            },
        )

    def download(self, book_id, save_path, fmt="txt", progress_cb=None, **kwargs):
        # 分卷导出：仅文库8插件内部分支，番茄/七猫插件根本没有这个参数。
        volume_mode = str(kwargs.get("volume_mode", "") or "whole")
        selected_volumes = kwargs.get("selected_volumes") or []
        if volume_mode in ("per_volume", "selected"):
            return self._download_volumes(
                book_id, save_path, fmt, progress_cb,
                kwargs, volume_mode, selected_volumes,
            )
        title = kwargs.get("title", "") or ""
        from gui.datastore import DataStore
        store = DataStore()

        def opt(key, default=None):
            return kwargs.get(key) or store.settings_get(f"epub_{key}", default)

        chapter_font = opt("chapter_font", "builtin:mashan") or "builtin:mashan"
        volume_font = opt("volume_font", "builtin:zhimang") or "builtin:zhimang"
        chapter_color = opt("chapter_color", "") or ""
        volume_color = opt("volume_color", "") or ""
        colorize = kwargs.get("colorize_dialogue")
        if colorize is None:
            colorize = bool(store.settings_get("epub_colorize_dialogue", True))
        chapter_hero = kwargs.get("chapter_hero")
        if chapter_hero is None:
            chapter_hero = bool(store.settings_get("epub_chapter_hero", False))
        hero_mode = opt("hero_mode", "rotate") or "rotate"
        hero_fixed = opt("hero_fixed", "") or ""
        chapter_tail = kwargs.get("chapter_tail")
        if chapter_tail is None:
            chapter_tail = bool(store.settings_get("epub_chapter_tail", False))
        tail_mode = opt("tail_mode", "rotate") or "rotate"
        tail_fixed = opt("tail_fixed", "") or ""
        smart_art = kwargs.get("smart_art")
        if smart_art is None:
            smart_art = bool(store.settings_get("epub_smart_art", True))

        source = Wenku8BookSource()
        try:
            result = build_txt(
                book_id=str(book_id),
                save_path=save_path,
                fmt=fmt,
                progress_cb=progress_cb,
                title_hint=title,
                author_hint=kwargs.get("author", "") or "",
                start_chapter=int(kwargs.get("start_chapter") or 0),
                end_chapter=int(kwargs.get("end_chapter") or 0),
                filename_template=kwargs.get("filename_template", "") or "",
                colorize_dialogue=bool(colorize),
                chapter_font=str(chapter_font or ""),
                volume_font=str(volume_font or ""),
                chapter_color=str(chapter_color or ""),
                volume_color=str(volume_color or ""),
                chapter_hero=bool(chapter_hero),
                hero_mode=str(hero_mode),
                hero_fixed=str(hero_fixed),
                hero_library="genshin",
                chapter_tail=bool(chapter_tail),
                tail_mode=str(tail_mode),
                tail_fixed=str(tail_fixed),
                smart_art=bool(smart_art),
                source=source,
            )
            # 严格模式：build_txt 渲染时遇到缺图会抛 QimaoError（图和书须一起
            # 成功），被下方 except 捕获判失败；走到这里说明正文和插图都齐全。
        except DownloadCancelled:
            raise
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            # 挑刺 #40：原 `not str(Wenku8CookieProbe.ok())` 恒为 False
            #（str(False)='False' 非空），登录提示分支永不生效。改为直接判布尔。
            if "login" in msg.lower() or "cookie" in msg.lower() or not Wenku8CookieProbe.ok():
                msg = ("文库8下载失败：登录态无效，请在「文库8登录」中"
                       "用账号密码或官网登录重新登录（无需手动填写 Cookie）。原因：" + msg)
            return DownloadResult(
                book_id=str(book_id),
                title=title or f"book_{book_id}",
                success=False,
                message=f"文库8下载失败: {msg}",
            )

        fmt_label = {"txt": "TXT", "chapter": "分章TXT", "epub": "EPUB"}.get(fmt, fmt)
        start, end = result.get("start_chapter", 1), result.get("end_chapter", result["total"])
        scoped = (start > 1 or end < result.get("total_chapters", 0))
        scope_text = f"第{start}-{end}章" if scoped else "全本"
        return DownloadResult(
            book_id=str(book_id),
            title=result["title"],
            success=True,
            file_path=result["file_path"],
            message=(f"已下载{scope_text} {result['chapters']}/{result['total']} 章"
                     f"（{fmt_label}·文库8，含全部插图）"),
            extra={
                "chapters": result["chapters"],
                "total": result["total"],
                "format": fmt,
                "real": True,
                "start_chapter": start,
                "end_chapter": end,
                "source": "wenku8",
                # 渲染层严格模式：缺图会在 build_txt 内抛 QimaoError 并被上方
                # except 判失败；走到这里正文和插图必已齐全，故失败数恒为 0。
                # （旧代码在此引用已被删除的 missing_imgs，导致整本下载渲染
                #   到 100% 后 NameError 崩溃、任务永远完不成——挑刺 #119）
                "images_failed": 0,
                # 作者回填：队列卡片显示 book.author，可能为空；EPUB 内作者
                # 来自详情。这里把详情作者带出去，由队列完成时回填任务。
                "author": str(result.get("author") or ""),
            },
        )


class Wenku8CookieProbe:
    """登录态探测缓存（仅用于失败提示，不影响下载主流程）。"""

    @staticmethod
    def ok() -> bool:
        try:
            from gui.wenku8_core.wenku8_auth import is_logged_in
            return is_logged_in()
        except Exception:  # noqa: BLE001
            return False


class Wenku8DownloaderPlugin(Plugin):
    meta = PluginMeta(
        name="文库8真实下载内核",
        version="1.0.0",
        author="WorkBuddy（自研内核）",
        description="文库8小说下载：目录/正文由自研内核提供，导出复用本项目全部功能",
        enabled=True,
    )

    def on_load(self, manager):
        self.log("文库8真实下载内核已就绪（自研内核）")

    def provide_downloader(self):
        return Wenku8Downloader()

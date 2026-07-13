"""
HOT PEPPER Beautyのブログから記事を1件選び、minimo(SALON TOOL)の
スナップフォトへ自動投稿する。1日1回、cron(毎日10:00)から呼び出される想定。

選び方:
    1. ブログの最新記事をチェックする。前回同期時と同じ記事なら何もしない(スキップ)。
    2. 新しい記事がある場合、その記事が「安全」(PayPay等の他社キャンペーン告知で
       はない)なら、その記事の画像・タイトルを投稿する。
    3. 新しい記事が「安全でない」場合は、直近のブログ記事群の中から安全なものを
       ランダムに1件選んで代わりに投稿する。
"""

import datetime
import os
import random
import re
import tempfile
import time
import urllib.request

from django.core.management.base import BaseCommand
from django.utils import timezone
from playwright.sync_api import Page, sync_playwright

from minimo_sync.models import SyncState

HPB_BLOG_BASE = "https://beauty.hotpepper.jp/kr/slnH000750485/blog/"
HPB_BLOG_PAGES = [
    HPB_BLOG_BASE,
    HPB_BLOG_BASE + "PN2.html",
    HPB_BLOG_BASE + "PN3.html",
    HPB_BLOG_BASE + "PN4.html",
    HPB_BLOG_BASE + "PN5.html",
]

# このキーワードを含むタイトルの記事は、他社キャンペーン告知など投稿に
# ふさわしくない可能性が高いとみなしてスキップする。
UNSAFE_TITLE_KEYWORDS = [
    "PayPay", "ペイペイ", "かなトク", "トクトク", "スクラッチ",
    "キャンペーン", "くじ", "クーポン", "総額",
]

MINIMODEL_STAFF_ID = "3b895cec80ac069390c943637c17815c6e091e1e741b5a5cb2b54064110d9ec5"
MINIMODEL_LOGIN_URL = "https://minimodel.jp/salontool/login"
MINIMODEL_PHOTOS_POST_URL = (
    f"https://minimodel.jp/salontool/home#/menu/staff/{MINIMODEL_STAFF_ID}/photos/post"
)

UA_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"}


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers=UA_HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode("utf-8", errors="replace")


def list_articles(html: str) -> list[dict]:
    items = re.findall(r'<li class="blogListCassette cFix">.*?</li>', html, re.S)
    articles = []
    for it in items:
        m = re.search(r'blogListTtl"><a href="([^"]+)">([^<]+)</a>', it)
        if not m:
            continue
        url, title = m.group(1), m.group(2)
        article_id_m = re.search(r"/blog/(bid[A-Za-z0-9]+)\.html", url)
        article_id = article_id_m.group(1) if article_id_m else url
        articles.append({"id": article_id, "url": url, "title": title})
    return articles


def is_safe(title: str) -> bool:
    return not any(kw in title for kw in UNSAFE_TITLE_KEYWORDS)


def fetch_article_detail(url: str) -> dict:
    html = fetch(url)
    title_m = re.search(r"<h1[^>]*>.*?「(.+?)」</h1>", html)
    img_m = re.search(r'<img src="([^"]*IMG_BLOG[^"]*)"', html)
    if not title_m or not img_m:
        raise RuntimeError(f"記事の解析に失敗しました: {url}")
    image_url = img_m.group(1).split("?")[0]
    return {"title": title_m.group(1), "image_url": image_url}


def pick_article_to_post(state: SyncState):
    """(投稿すべきかどうか, 選ばれた記事詳細 or None, 最新記事ID) を返す。"""
    first_page_html = fetch(HPB_BLOG_PAGES[0])
    articles = list_articles(first_page_html)
    if not articles:
        raise RuntimeError("ブログ記事一覧を取得できませんでした。")

    latest = articles[0]
    if latest["id"] == state.last_blog_article_id:
        return False, None, latest["id"]

    if is_safe(latest["title"]):
        return True, latest, latest["id"]

    # 最新記事が安全でない場合、直近数ページから安全な記事をランダムに選ぶ
    pool = []
    for page_url in HPB_BLOG_PAGES:
        html = first_page_html if page_url == HPB_BLOG_PAGES[0] else fetch(page_url)
        pool.extend(a for a in list_articles(html) if is_safe(a["title"]))

    if not pool:
        raise RuntimeError("投稿に安全な記事が見つかりませんでした。")

    return True, random.choice(pool), latest["id"]


def minimo_login(page: Page, salon_id: str, password: str) -> None:
    page.goto(MINIMODEL_LOGIN_URL, timeout=20000, wait_until="domcontentloaded")
    page.fill('input[name="salon_id"]', salon_id)
    page.fill('input[name="password"]', password)
    page.click('input[type="submit"]')
    page.wait_for_load_state("domcontentloaded", timeout=20000)
    time.sleep(2)


def minimo_post_snapphoto(page: Page, image_path: str, comment: str) -> None:
    page.goto(MINIMODEL_PHOTOS_POST_URL, timeout=20000, wait_until="domcontentloaded")
    time.sleep(1.5)
    page.set_input_files('input[type="file"]', image_path)
    time.sleep(2)
    page.locator('label.a_labeled_input', has_text="その他").locator('input[type="checkbox"]').check(timeout=10000)
    page.locator("textarea").first.fill(comment)
    time.sleep(1)
    page.get_by_role("button", name="保存").click(timeout=10000)
    time.sleep(3)


class Command(BaseCommand):
    help = "HOT PEPPER Beautyブログの記事をminimoのスナップフォトへ自動投稿する"

    def handle(self, *args, **options):
        state, _ = SyncState.objects.get_or_create(pk=1)

        try:
            should_post, article, latest_id = pick_article_to_post(state)
        except Exception as e:
            self.stderr.write(f"記事選定エラー: {e}")
            state.last_result = f"error(select): {e}"
            state.last_synced_at = timezone.now()
            state.save()
            return

        if not should_post:
            self.stdout.write("新しい記事がないためスキップします。")
            state.last_result = "skipped: no new article"
            state.last_synced_at = timezone.now()
            state.save()
            return

        try:
            detail = fetch_article_detail(article["url"])
        except Exception as e:
            self.stderr.write(f"記事詳細取得エラー: {e}")
            state.last_result = f"error(detail): {e}"
            state.last_synced_at = timezone.now()
            state.save()
            return

        salon_id = os.environ["MINIMODEL_SALON_ID"]
        password = os.environ["MINIMODEL_PASSWORD"]

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            req = urllib.request.Request(detail["image_url"], headers=UA_HEADERS)
            with urllib.request.urlopen(req, timeout=20) as resp:
                tmp.write(resp.read())
            image_path = tmp.name

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(locale="ja-JP")
                page = context.new_page()
                minimo_login(page, salon_id, password)
                minimo_post_snapphoto(page, image_path, detail["title"])
                browser.close()
        except Exception as e:
            self.stderr.write(f"投稿エラー: {e}")
            state.last_blog_article_id = latest_id
            state.last_result = f"error(post): {e}"
            state.last_synced_at = timezone.now()
            state.save()
            return
        finally:
            os.unlink(image_path)

        self.stdout.write(f"投稿完了: {detail['title']}")
        state.last_blog_article_id = latest_id
        state.last_posted_title = detail["title"]
        state.last_result = "posted"
        state.last_synced_at = timezone.now()
        state.save()

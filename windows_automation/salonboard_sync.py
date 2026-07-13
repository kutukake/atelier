"""
salonboard.com にログインし、「受付締切」を当日の2時間前に設定し続けるスクリプト。

使い方:
    python salonboard_sync.py            # ヘッドレスで実行(タスクスケジューラ用)
    python salonboard_sync.py --headed   # ブラウザ画面を表示して実行(動作確認用)

失敗した場合のみ debug/ フォルダにHTMLを保存する(スクリーンショットは撮らない。
このサイトはトラッキング通信が延々続くため、フルページスクリーンショットが
「フォント読み込み待ち」で数十秒〜固まることがあり、実行時間を大きく損なうため)。
"""

import datetime
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import Page, sync_playwright

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

LOGIN_ID = os.environ["SALONBOARD_LOGIN_ID"]
PASSWORD = os.environ["SALONBOARD_PASSWORD"]

LOGIN_URL = "https://salonboard.com/login/"
SALON_SETUP_URL = "https://salonboard.com/KLP/set/salonSetup/"

# 「受付締切」を当日の何時間前にするか。店舗設定ページの
# select[name=baseTimeOfWebToTodayTime] の option value と対応する。
#   0000=直前 0030=30分前 0100=1時間前 0130=1時間30分前
#   0200=2時間前 0230=2時間30分前 0300=3時間前 0330=3時間30分前
TARGET_DEADLINE_VALUE = "0200"  # 2時間前

DEBUG_DIR = BASE_DIR / "debug"
DEBUG_DIR.mkdir(exist_ok=True)

# salonboard.com はAkamaiのボット対策があり、UA/Client Hintsが実ブラウザと
# 整合していないとアクセスがブロックされることがあるため、実在するWindows版
# Chromeのものに合わせている。
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
EXTRA_HEADERS = {
    "sec-ch-ua": '"Chromium";v="126", "Not.A/Brand";v="24", "Google Chrome";v="126"',
    "sec-ch-ua-platform": '"Windows"',
    "accept-language": "ja-JP,ja;q=0.9",
}


def log(msg: str) -> None:
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def dump_html_on_error(page: Page, name: str) -> None:
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    try:
        (DEBUG_DIR / f"{ts}_{name}.html").write_text(page.content(), encoding="utf-8")
    except Exception as e:
        log(f"[dump警告] {name}: {e}")


def wait_until_url_changes(page: Page, old_url: str, timeout: float = 25) -> bool:
    """page.urlがold_urlから変わるまで軽くポーリングする。

    Playwrightの組み込みの待ち(wait_for_load_state等)は、このサイトの
    継続的なトラッキング通信のせいで固まることがあるため使わない。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if page.url != old_url:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def login(page: Page) -> None:
    log("ログインページへ移動します")
    page.goto(LOGIN_URL, timeout=30000, wait_until="domcontentloaded")
    # Akamaiのボット対策センサーがページ滞在中にデータを収集する時間を
    # 与えるため、即座にログインしない(人間の操作を模す)。
    time.sleep(3)

    page.fill('input[name="userId"]', LOGIN_ID)
    time.sleep(0.4)
    page.fill('input[name="password"]', PASSWORD)
    time.sleep(1.5)

    log("ログインボタンを押します")
    old_url = page.url
    # 通常のclick()はログイン後の計測用リクエスト(GA/Akamaiセンサー等)の
    # 完了待ちで固まるため、no_wait_afterで明示的に切り離す。
    page.click("a.common-CNCcommon__primaryBtn.loginBtnSize", no_wait_after=True)

    if not wait_until_url_changes(page, old_url, timeout=45):
        dump_html_on_error(page, "login_timeout")
        raise RuntimeError("ログイン後のページ遷移がタイムアウトしました。")

    log(f"ログイン成功。現在のURL: {page.url}")
    close_survey_popup(page)


def close_survey_popup(page: Page) -> None:
    """ログイン後に出る満足度アンケート等のポップアップを閉じる(出ないこともある)。"""
    try:
        close_btn = page.locator("text=×").first
        if close_btn.count() > 0 and close_btn.is_visible(timeout=2000):
            log("ポップアップを閉じます")
            close_btn.click(timeout=3000)
    except Exception:
        pass


def open_deadline_setting(page: Page) -> None:
    log("店舗設定ページへ直接移動します")
    page.goto(SALON_SETUP_URL, timeout=30000, wait_until="domcontentloaded")

    if "エラー" in page.title():
        dump_html_on_error(page, "settings_error")
        raise RuntimeError("店舗設定ページでエラー画面になりました(セッション切れの可能性)。")


def set_deadline_and_save(page: Page) -> None:
    current_type = page.locator('input[name="webToType"]:checked').get_attribute("value")
    current_time = page.locator('select[name="baseTimeOfWebToTodayTime"]').input_value()

    if current_type == "1" and current_time == TARGET_DEADLINE_VALUE:
        log("受付締切は既に「当日の2時間前」になっています。変更不要です。")
        return

    log(
        f"受付締切を「当日の2時間前」に変更します"
        f"(現在: webToType={current_type}, baseTimeOfWebToTodayTime={current_time})"
    )
    page.check('input[name="webToType"][value="1"]')
    page.select_option('select[name="baseTimeOfWebToTodayTime"]', TARGET_DEADLINE_VALUE)

    log("「設定する」ボタンを押します")
    page.click("a#complete", no_wait_after=True)
    time.sleep(3)
    log("設定を保存しました")


def main() -> None:
    headless = "--headed" not in sys.argv
    print(f"===== {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} 実行開始 =====", flush=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            locale="ja-JP",
            user_agent=USER_AGENT,
            extra_http_headers=EXTRA_HEADERS,
        )
        page = context.new_page()
        try:
            login(page)
            open_deadline_setting(page)
            set_deadline_and_save(page)
            log("完了しました")
        except Exception as e:
            log(f"エラーが発生しました: {e}")
            dump_html_on_error(page, "error")
            raise
        finally:
            browser.close()


if __name__ == "__main__":
    main()

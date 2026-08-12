from __future__ import annotations

import base64
import html
import io
import os
import re

from django.contrib import admin
from django.http import HttpResponse
from django.urls import path
from django.views.decorators.csrf import csrf_exempt
from google import genai
from google.genai import types
from PIL import Image, ImageChops, ImageFilter

from config.coupons import COUPONS
from config.gemini_prompts import GEMINI_FOOTER, build_question

TITLE_MAX_LEN = 25
BODY_TARGET_LEN = 900
BODY_MAX_LEN = 1000
MAX_RETRIES = 3

PAGE_HTML = """
<div style="max-width:640px; margin:40px auto; padding:0 20px; font-family:sans-serif; text-align:center;">
  <h1 style="font-size:22px; margin-bottom:8px;">アトリエ横浜店 投稿コンテンツ作成</h1>
  <p style="color:#666; margin-bottom:28px;">
    ボタンを押すと、登録済みのクーポン内容をもとに紹介文とイメージ画像をまとめて生成します。
  </p>
  <button type="button" id="createBtn" style="
    padding:12px 32px; font-size:16px; color:#fff; background:#3b82f6;
    border:none; border-radius:6px; cursor:pointer;">作成</button>
  <span id="spinner" style="display:none;">
    <span style="
      display:inline-block; width:16px; height:16px; margin-left:8px;
      border:3px solid #ccc; border-top-color:#333; border-radius:50%;
      animation:spin 0.8s linear infinite; vertical-align:middle;"></span>
    作成中...
  </span>
</div>
<div id="result" style="max-width:960px; margin:0 auto; padding:0 20px;"></div>
<style>
@keyframes spin { to { transform: rotate(360deg); } }
</style>
<script>
function copyGeneratedText(btn) {
    var textarea = btn.previousElementSibling;
    navigator.clipboard.writeText(textarea.value).then(function () {
        var original = btn.textContent;
        btn.textContent = "コピーしました";
        setTimeout(function () { btn.textContent = original; }, 1500);
    });
}

document.getElementById("createBtn").addEventListener("click", function () {
    var btn = document.getElementById("createBtn");
    var spinner = document.getElementById("spinner");
    var result = document.getElementById("result");
    btn.style.display = "none";
    spinner.style.display = "inline-block";
    result.innerHTML = "";
    fetch(window.location.pathname, { method: "POST" })
        .then(function (res) { return res.text(); })
        .then(function (html) { result.innerHTML = html; })
        .catch(function (err) { result.innerHTML = "<p>エラー: " + err + "</p>"; })
        .finally(function () {
            spinner.style.display = "none";
            btn.style.display = "inline-block";
        });
});
</script>
"""


def ask_gemini(client, prompt: str) -> str:
    response = client.models.generate_content(model="gemini-flash-latest", contents=prompt)
    return response.text.strip()


def split_title_body(text: str) -> tuple[str, str]:
    lines = text.strip().splitlines()
    title_line = lines[0].strip()
    body = "\n".join(lines[1:]).strip()
    # 「タイトル（N文字）」の（N文字）表記を取り除いて実際のタイトル文だけにする
    title = re.sub(r"[（(]\s*\d+\s*文字[）)]\s*$", "", title_line).strip()
    return title, body


def truncate_text(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    truncated = text[:max_len]
    last_newline = truncated.rfind("\n")
    if last_newline > 0:
        truncated = truncated[:last_newline]
    return truncated.rstrip()


def generate_article(client, coupon: dict) -> tuple[str, str]:
    text = ask_gemini(client, build_question(coupon["title"], coupon["body"]))
    title, body = split_title_body(text)

    for _ in range(MAX_RETRIES):
        if len(title) <= TITLE_MAX_LEN:
            break
        title = ask_gemini(
            client,
            f"以下のタイトルを、意味を変えずに{TITLE_MAX_LEN}文字以内になるよう短く調整してください。"
            f"調整後のタイトルの文字列だけを出力してください。\n\nタイトル: {title}",
        )
        title = re.sub(r"[（(]\s*\d+\s*文字[）)]\s*$", "", title.strip()).strip()
    title = truncate_text(title, TITLE_MAX_LEN)

    for _ in range(MAX_RETRIES):
        if len(body) <= BODY_TARGET_LEN:
            break
        body = ask_gemini(
            client,
            f"以下の本文を、■の見出し構成を保ったまま、{BODY_TARGET_LEN}文字以内になるよう短く調整してください。"
            f"調整後の本文だけを出力してください。\n\n本文:\n{body}",
        )
    body = truncate_text(body, BODY_MAX_LEN)

    return title, body


BACKGROUND_CROP_TOLERANCE = 30
IMAGE_MIN_WIDTH = 480
IMAGE_MIN_HEIGHT = 400


def crop_background(image_bytes: bytes) -> bytes:
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    width, height = image.size
    corners = [
        image.getpixel((0, 0)),
        image.getpixel((width - 1, 0)),
        image.getpixel((0, height - 1)),
        image.getpixel((width - 1, height - 1)),
    ]
    bg_color = tuple(sum(c[i] for c in corners) // len(corners) for i in range(3))

    diff = ImageChops.difference(image, Image.new("RGB", image.size, bg_color))
    r, g, b = diff.split()
    max_diff = ImageChops.lighter(ImageChops.lighter(r, g), b)
    mask = max_diff.point(lambda p: 255 if p > BACKGROUND_CROP_TOLERANCE else 0)
    # 孤立したノイズ画素がbboxを不必要に広げないよう、収縮フィルタで除去してから範囲を求める
    mask = mask.filter(ImageFilter.MinFilter(7))
    bbox = mask.getbbox()

    cropped = image.crop(bbox) if bbox else image

    width, height = cropped.size
    scale = max(IMAGE_MIN_WIDTH / width, IMAGE_MIN_HEIGHT / height, 1.0)
    if scale > 1.0:
        cropped = cropped.resize(
            (round(width * scale), round(height * scale)), Image.LANCZOS
        )

    buffer = io.BytesIO()
    cropped.save(buffer, format="PNG")
    return buffer.getvalue()


def generate_ticket_image(client, title: str, body: str) -> tuple[bytes, str] | None:
    prompt = (
        "以下のサロンサービスの内容をイメージした、チケット風のイラストを1枚生成してください。"
        "背景は白色の無地(模様や陰影のない単色)にしてください。"
        "画像内にテキスト・文字・ロゴ・数字は一切含めないでください。イラストのみにしてください。\n\n"
        f"タイトル: {title}\n本文: {body}"
    )
    response = client.models.generate_content(
        model="gemini-2.5-flash-image",
        contents=prompt,
        config=types.GenerateContentConfig(
            image_config=types.ImageConfig(aspect_ratio="16:9"),
        ),
    )
    for part in response.candidates[0].content.parts:
        if part.inline_data is not None:
            cropped_bytes = crop_background(part.inline_data.data)
            return cropped_bytes, "image/png"
    return None


def error_block(label: str, error_text: str) -> str:
    return (
        f"<p>{label}: {html.escape(error_text)}</p>"
        f'<textarea style="display:none;">{html.escape(f"{label}: {error_text}")}</textarea>'
        '<button type="button" onclick="copyGeneratedText(this)">コピー</button>'
    )


def render_one(text_client, image_client, index: int, coupon: dict) -> str:
    title = body = None
    try:
        title, body = generate_article(text_client, coupon)
        full_text = f"{title}\n\n{body}\n\n{GEMINI_FOOTER}"
        text_html = (
            '<textarea rows="14" style="width:100%; box-sizing:border-box; padding:8px; '
            'font-family:inherit; font-size:14px;">'
            f"{html.escape(full_text)}</textarea>"
            '<button type="button" onclick="copyGeneratedText(this)">コピー</button>'
        )
    except Exception as e:
        text_html = error_block("テキスト生成エラー", str(e))

    image_html = ""
    if title is not None:
        try:
            image_result = generate_ticket_image(image_client, title, body)
            if image_result:
                image_bytes, mime_type = image_result
                b64 = base64.b64encode(image_bytes).decode("ascii")
                image_html = (
                    f'<img src="data:{mime_type};base64,{b64}" '
                    'style="width:100%; max-width:480px; height:auto; display:block;">'
                )
            else:
                image_html = error_block("画像生成エラー", "応答に画像が含まれていませんでした")
        except Exception as e:
            image_html = error_block("画像生成エラー", str(e))

    return (
        f"<h3>{index}件目</h3>"
        '<div style="display:flex; gap:24px; align-items:flex-start; flex-wrap:wrap;">'
        f'<div style="flex:1; min-width:280px;">{text_html}</div>'
        f'<div style="flex:0 0 auto; width:480px; max-width:100%;">{image_html}</div>'
        "</div><hr>"
    )


@csrf_exempt
def test_view(request):
    if request.method == "POST":
        try:
            text_client = genai.Client(api_key=os.environ["GEMINI_API_KEY_TEXT"])
            image_client = genai.Client(api_key=os.environ["GEMINI_API_KEY_IMAGE"])
        except Exception as e:
            return HttpResponse(error_block("Geminiクライアント作成エラー", str(e)))

        blocks = [
            render_one(text_client, image_client, i, coupon)
            for i, coupon in enumerate(COUPONS, 1)
        ]
        return HttpResponse("".join(blocks))

    return HttpResponse(PAGE_HTML)


urlpatterns = [
    path("admin/", admin.site.urls),
    path("", test_view),
]

# salonboard 受付締切 自動設定スクリプト (Windows実行用)

WSL2/Docker環境からは `salonboard.com` への通信がブロックされる(TLSハンドシェイク後に
応答がハングする)ことが確認されたため、このスクリプトはWindows側のPythonで直接実行する。
Djangoプロジェクト(atelier-web / atelier-db)とは独立して動作する。

## セットアップ手順(初回のみ)

1. Windowsに Python 3.11以上をインストールする(https://www.python.org/downloads/ 、
   インストール時に「Add python.exe to PATH」にチェック)。

2. エクスプローラーのアドレス欄に `\\wsl$` と入力し、このプロジェクトの
   `windows_automation` フォルダまで移動する。
   (パスの例: `\\wsl$\Ubuntu\home\kutu\myService\atelierProject\windows_automation`)

3. このフォルダ内で Shift+右クリック →「PowerShellウィンドウをここで開く」
   (または「ターミナルで開く」)。

4. 以下を順番に実行する。

   ```powershell
   python -m venv venv
   venv\Scripts\activate
   pip install -r requirements.txt
   playwright install chromium
   ```

5. `.env` ファイルにログインIDとパスワードが入っているか確認する
   (`.env.example` を参考に、なければ作成する)。

## 動作確認(初回は必ずヘッドありで)

```powershell
python salonboard_sync.py --headed
```

ブラウザ画面が実際に開き、ログイン→設定→受付締切の変更→保存、という流れが
目視で確認できる。途中で失敗した場合も含め、実行のたびに `debug/` フォルダに
各ステップのスクリーンショットとHTMLが保存される。

**サイトの実際のボタン名・リンク名が想定と違うと途中で止まる。** その場合は
`debug/` 内の一番新しい `*_error.png` / `*_error.html` を見て、
`salonboard_sync.py` 内の該当箇所(`login` / `open_deadline_setting` /
`set_deadline_and_save` 関数)のテキスト検索条件を実際の画面表示に合わせて
修正する。

## 定期実行の登録(動作確認できたら)

```powershell
powershell -ExecutionPolicy Bypass -File .\register_task.ps1
```

タスクスケジューラに `AtelierSalonboardSync` という名前で1時間ごとに実行する
タスクが登録される。以後は `python salonboard_sync.py`(ヘッドレス)が自動で
毎時実行される。

- 停止したい場合: タスクスケジューラのアプリで `AtelierSalonboardSync` を無効化/削除する。
- 実行結果を見たい場合: `debug\task_log.txt` に毎回の実行ログ(「変更不要でした」
  「設定を保存しました」等)が追記されていく。加えて `debug/` フォルダに実行のたびの
  スクリーンショット・HTMLも溜まっていく(どちらも古いものは適宜手動で削除して問題ない)。
- 正しく登録できたか確認したい場合: タスクスケジューラのアプリ(`taskschd.msc`)を開き、
  「タスク スケジューラ ライブラリ」から `AtelierSalonboardSync` を探す。右クリック→
  「実行」で即座に1回試すこともできる。

## 注意事項

- `.env` にはsalonboardのログイン情報が平文で入っている。このフォルダをそのまま
  他人に渡したり、Gitにコミットしたりしないこと(`.gitignore` で除外済み)。
- 自動ログイン・自動設定変更は salonboard の利用規約に抵触する可能性がある。
  自己責任で利用すること。

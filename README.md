# atelierProject

Django + MySQL、Docker Compose構成。

- web コンテナ: `atelier-web` (Django, http://localhost:8000)
- db コンテナ: `atelier-db` (MySQL 8.4, localhost:3306)

## セットアップ

```bash
cp .env.example .env   # 必要に応じて値を編集
docker compose up --build
```

初回起動時に `entrypoint.sh` がDBの起動を待ってから `migrate` を自動実行します。

## よく使うコマンド

```bash
# バックグラウンド起動
docker compose up -d --build

# 管理ユーザー作成
docker compose exec web python manage.py createsuperuser

# マイグレーション作成
docker compose exec web python manage.py makemigrations

# ログ確認
docker compose logs -f web

# 停止
docker compose down

# 停止 + DBデータも削除
docker compose down -v
```

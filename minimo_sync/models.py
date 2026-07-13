from django.db import models


class SyncState(models.Model):
    """直近の同期状態を1行だけ保持する(シングルトン的に使う)。"""

    last_blog_article_id = models.CharField(max_length=64, blank=True, default="")
    last_posted_title = models.CharField(max_length=255, blank=True, default="")
    last_synced_at = models.DateTimeField(null=True, blank=True)
    last_result = models.CharField(max_length=255, blank=True, default="")

    def __str__(self):
        return f"SyncState(last_blog_article_id={self.last_blog_article_id!r}, last_synced_at={self.last_synced_at})"

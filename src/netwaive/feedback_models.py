from __future__ import annotations

import uuid
from django.conf import settings
from django.db import models


class ResponseFeedback(models.Model):
    RATING_CHOICES = (("up", "Useful"), ("down", "Needs improvement"))

    response_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="netwaive_feedback")
    conversation_id = models.UUIDField(db_index=True)
    rating = models.CharField(max_length=4, choices=RATING_CHOICES)
    reason = models.CharField(max_length=500, blank=True)
    expected_answer = models.TextField(blank=True)
    prompt = models.TextField(blank=True)
    answer = models.TextField()
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-updated",)

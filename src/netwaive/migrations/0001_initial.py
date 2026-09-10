from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):
    initial = True
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel(
            name="ResponseFeedback",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("response_id", models.UUIDField(db_index=True, default=uuid.uuid4, unique=True)),
                ("conversation_id", models.UUIDField(db_index=True)),
                ("rating", models.CharField(choices=[("up", "Useful"), ("down", "Needs improvement")], max_length=4)),
                ("reason", models.CharField(blank=True, max_length=500)),
                ("expected_answer", models.TextField(blank=True)),
                ("prompt", models.TextField(blank=True)),
                ("answer", models.TextField()),
                ("created", models.DateTimeField(auto_now_add=True)),
                ("updated", models.DateTimeField(auto_now=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="netwaive_feedback", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ("-updated",)},
        )
    ]

from django.urls import path
from . import views

urlpatterns = [
    path("chat/", views.chat, name="chat"),
    path("api/chat/", views.chat_api, name="chat_api"),
    path("api/history/", views.history_api, name="history"),
    path("api/ui/", views.ui_api, name="ui"),
    path("api/sessions/new/", views.session_new_api, name="session_new"),
    path("api/sessions/select/", views.session_select_api, name="session_select"),
    path("api/sessions/delete/", views.session_delete_api, name="session_delete"),
    path("api/reset/", views.reset_api, name="reset"),
    path("api/health/", views.health_api, name="health"),
]

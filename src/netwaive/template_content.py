from django.utils.translation import get_language
from netbox.plugins import PluginTemplateExtension
from . import NetWAIveConfig


class NetWAIveFloatingWidget(PluginTemplateExtension):
    def navbar(self):
        english = str(get_language() or "").lower().startswith("en")
        banner = "NetBox Assistant (Beta - under active development). Read/write based on global configuration. Changes require your confirmation." if english else "Assistant NetBox (Beta - en cours de développement). Lecture/écriture selon la configuration globale. Les modifications requièrent votre confirmation."
        title = "NetBox Assistant (Beta)" if english else "Assistant NetBox (Beta)"
        return self.render(
            "netwaive/floating_widget.html",
            extra_context={"plugin_version": NetWAIveConfig.version, "banner": banner, "widget_title": title},
        )


template_extensions = [NetWAIveFloatingWidget]

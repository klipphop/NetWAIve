from netbox.plugins import PluginTemplateExtension
from . import NetWAIveConfig


class NetWAIveFloatingWidget(PluginTemplateExtension):
    def navbar(self):
        return self.render(
            "netwaive/floating_widget.html",
            extra_context={"plugin_version": NetWAIveConfig.version},
        )


template_extensions = [NetWAIveFloatingWidget]

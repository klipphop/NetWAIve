from netbox.plugins import PluginTemplateExtension
from . import NetBoxLLMChatConfig


class NetBoxLLMChatFloatingWidget(PluginTemplateExtension):
    def navbar(self):
        return self.render(
            "netbox_llm_chat/floating_widget.html",
            extra_context={"plugin_version": NetBoxLLMChatConfig.version},
        )


template_extensions = [NetBoxLLMChatFloatingWidget]

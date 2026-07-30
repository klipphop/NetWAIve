class NetBoxChatError(Exception):
    """Erreur utilisateur lisible."""


class ObjectNotFound(NetBoxChatError):
    pass


class AmbiguousObject(NetBoxChatError):
    pass


class ToolValidationError(NetBoxChatError):
    pass

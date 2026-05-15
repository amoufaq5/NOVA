"""Nova runtime environment — variable scoping and lookup."""

from ..types.values import NovaValue


class Environment:
    def __init__(self, parent: 'Environment | None' = None):
        self.parent = parent
        self.bindings: dict[str, NovaValue] = {}

    def define(self, name: str, value):
        self.bindings[name] = value

    def get(self, name: str):
        if name in self.bindings:
            return self.bindings[name]
        if self.parent:
            return self.parent.get(name)
        raise NameError(f"Undefined variable: '{name}'")

    def set(self, name: str, value):
        if name in self.bindings:
            self.bindings[name] = value
            return
        if self.parent:
            self.parent.set(name, value)
            return
        raise NameError(f"Undefined variable: '{name}'")

    def has(self, name: str) -> bool:
        if name in self.bindings:
            return True
        if self.parent:
            return self.parent.has(name)
        return False

    def child(self) -> 'Environment':
        return Environment(parent=self)

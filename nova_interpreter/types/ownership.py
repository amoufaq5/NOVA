"""Nova runtime ownership manager — enforces soft ownership rules with arena support."""

from .values import NovaPanic


class OwnershipError(NovaPanic):
    pass


class OwnedValue:
    __slots__ = ("value", "moved", "borrow_count", "mut_borrowed", "owner_scope", "arena_id")

    def __init__(self, value, owner_scope: int, arena_id: int = 0):
        self.value = value
        self.moved = False
        self.borrow_count = 0
        self.mut_borrowed = False
        self.owner_scope = owner_scope
        self.arena_id = arena_id


class OwnershipManager:
    def __init__(self):
        self._next_scope_id = 0
        self._scope_stack: list[int] = []
        self._registry: dict[tuple[int, str], OwnedValue] = {}

    @property
    def current_scope(self) -> int:
        return self._scope_stack[-1] if self._scope_stack else 0

    def enter_scope(self) -> int:
        self._next_scope_id += 1
        self._scope_stack.append(self._next_scope_id)
        return self._next_scope_id

    def exit_scope(self) -> list:
        if not self._scope_stack:
            return []
        scope_id = self._scope_stack.pop()
        freed = []
        to_remove = []
        for key, owned in self._registry.items():
            if key[0] == scope_id:
                if not owned.moved:
                    if owned.borrow_count > 0:
                        raise OwnershipError(
                            f"Cannot free '{key[1]}': still has "
                            f"{owned.borrow_count} active borrow(s)"
                        )
                    freed.append(owned.value)
                to_remove.append(key)
        for key in to_remove:
            del self._registry[key]
        return freed

    def register(self, name: str, value, scope_id: int | None = None):
        sid = scope_id or self.current_scope
        self._registry[(sid, name)] = OwnedValue(value, sid)

    def lookup(self, name: str) -> OwnedValue | None:
        for scope_id in reversed(self._scope_stack):
            key = (scope_id, name)
            if key in self._registry:
                return self._registry[key]
        if (0, name) in self._registry:
            return self._registry[(0, name)]
        return None

    def access(self, name: str):
        owned = self.lookup(name)
        if owned is None:
            return None
        if owned.moved:
            raise OwnershipError(f"Use of moved value '{name}'")
        return owned.value

    def borrow(self, name: str):
        owned = self.lookup(name)
        if owned is None:
            return None
        if owned.moved:
            raise OwnershipError(f"Cannot borrow moved value '{name}'")
        if owned.mut_borrowed:
            raise OwnershipError(f"Cannot immutably borrow '{name}': already mutably borrowed")
        owned.borrow_count += 1
        return owned.value

    def borrow_mut(self, name: str):
        owned = self.lookup(name)
        if owned is None:
            return None
        if owned.moved:
            raise OwnershipError(f"Cannot borrow moved value '{name}'")
        if owned.borrow_count > 0:
            raise OwnershipError(
                f"Cannot mutably borrow '{name}': "
                f"{owned.borrow_count} immutable borrow(s) exist"
            )
        if owned.mut_borrowed:
            raise OwnershipError(f"Cannot mutably borrow '{name}': already mutably borrowed")
        owned.mut_borrowed = True
        return owned.value

    def release_borrow(self, name: str):
        owned = self.lookup(name)
        if owned and owned.borrow_count > 0:
            owned.borrow_count -= 1

    def release_mut_borrow(self, name: str):
        owned = self.lookup(name)
        if owned:
            owned.mut_borrowed = False

    def move(self, name: str):
        owned = self.lookup(name)
        if owned is None:
            raise OwnershipError(f"Cannot move unknown value '{name}'")
        if owned.moved:
            raise OwnershipError(f"Cannot move '{name}': already moved")
        if owned.borrow_count > 0 or owned.mut_borrowed:
            raise OwnershipError(f"Cannot move '{name}': active borrows exist")
        owned.moved = True
        return owned.value

    def transfer(self, from_name: str, to_name: str, to_scope: int | None = None):
        value = self.move(from_name)
        self.register(to_name, value, to_scope)
        return value

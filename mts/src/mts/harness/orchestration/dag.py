"""RoleDAG — topological sort with parallel batch computation."""

from __future__ import annotations

from mts.harness.orchestration.types import RoleSpec


class RoleDAG:
    def __init__(self, roles: list[RoleSpec]) -> None:
        self._roles = {r.name: r for r in roles}
        self._names = [r.name for r in roles]

    def validate(self) -> None:
        """Check for missing deps, self-deps, and cycles."""
        for role in self._roles.values():
            for dep in role.depends_on:
                if dep == role.name:
                    raise ValueError(f"Role '{role.name}' depends on itself")
                if dep not in self._roles:
                    raise ValueError(f"Role '{role.name}' depends on unknown role '{dep}'")
        # Cycle detection via topological sort attempt
        self.execution_batches()

    def execution_batches(self) -> list[list[str]]:
        """Return batches of role names for execution. Each batch can run in parallel."""
        in_degree: dict[str, int] = {n: 0 for n in self._names}
        for role in self._roles.values():
            for _dep in role.depends_on:
                in_degree[role.name] += 1

        remaining = set(self._names)
        batches: list[list[str]] = []

        while remaining:
            batch = sorted(n for n in remaining if in_degree[n] == 0)
            if not batch:
                raise ValueError(f"Cycle detected among roles: {remaining}")
            batches.append(batch)
            remaining -= set(batch)
            for name in batch:
                for role in self._roles.values():
                    if name in role.depends_on and role.name in remaining:
                        in_degree[role.name] -= 1

        return batches

    def add_role(self, role: RoleSpec) -> None:
        """Add a role to the DAG. Validates no duplicates, no self-deps, no missing deps, no cycles."""
        if role.name in self._roles:
            raise ValueError(f"Role '{role.name}' already exists in DAG")
        for dep in role.depends_on:
            if dep == role.name:
                raise ValueError(f"Role '{role.name}' depends on itself")
            if dep not in self._roles:
                raise ValueError(f"Role '{role.name}' depends on unknown role '{dep}'")
        self._roles[role.name] = role
        self._names.append(role.name)
        # Validate no cycles were introduced
        try:
            self.execution_batches()
        except ValueError:
            # Rollback
            del self._roles[role.name]
            self._names.remove(role.name)
            raise

    def remove_role(self, name: str) -> None:
        """Remove a role from the DAG. Fails if other roles depend on it."""
        if name not in self._roles:
            raise ValueError(f"Role '{name}' not found in DAG")
        dependents = [r.name for r in self._roles.values() if name in r.depends_on]
        if dependents:
            raise ValueError(f"Role '{name}' is depended on by: {', '.join(dependents)}")
        del self._roles[name]
        self._names.remove(name)

    @property
    def roles(self) -> dict[str, RoleSpec]:
        return dict(self._roles)

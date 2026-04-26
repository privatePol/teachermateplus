from __future__ import annotations

from django.urls import NoReverseMatch, reverse

from apps.core.services.permissions import PermissionService
from django.db.models import Prefetch

from apps.navigation.models import MenuGroup, MenuItem


class MenuService:
    @staticmethod
    def _item_url(route_name: str | None) -> str:
        if not route_name:
            return "#"
        try:
            return reverse(route_name)
        except NoReverseMatch:
            return "#"

    @classmethod
    def get_menu_tree(
        cls,
        user,
        portal: str,
        tenant_id: int | None = None,
        campus_id: int | None = None,
        effective_codes=None,
    ):
        item_queryset = (
            MenuItem.objects.filter(is_active=True)
            .select_related("parent")
            .prefetch_related("menuitempermission_set__permission")
            .order_by("sort_order", "id")
        )
        groups = (
            MenuGroup.objects.filter(portal=portal, is_active=True)
            .order_by("sort_order", "id")
            .prefetch_related(Prefetch("items", queryset=item_queryset, to_attr="active_items"))
        )
        if effective_codes is None:
            effective_codes = PermissionService.get_effective_permission_codes(
                user, tenant_id=tenant_id, campus_id=campus_id
            )

        output = []
        for group in groups:
            items = list(getattr(group, "active_items", []))
            item_map = {}
            for item in items:
                required_codes = set(
                    item.menuitempermission_set.values_list("permission__code", flat=True)
                )
                is_visible = not required_codes or bool(required_codes & effective_codes)
                item_map[item.id] = {
                    "item": item,
                    "url": cls._item_url(item.route_name),
                    "children": [],
                    "visible": is_visible,
                }

            for item in items:
                if item.parent_id and item.parent_id in item_map:
                    item_map[item.parent_id]["children"].append(item_map[item.id])

            roots = [node for node in item_map.values() if not node["item"].parent_id and node["visible"]]
            # Keep parents with at least one visible child.
            for node in roots:
                node["children"] = [child for child in node["children"] if child["visible"]]
                if not node["visible"] and not node["children"]:
                    continue
                node["visible"] = node["visible"] or bool(node["children"])

            roots = [node for node in roots if node["visible"]]
            if roots:
                output.append({"group": group, "items": roots})
        return output

from django.db import models

from apps.core.models import ActivatableModel, TimeStampedModel


class MenuGroup(TimeStampedModel, ActivatableModel):
    class Portal(models.TextChoices):
        ADMIN = "ADMIN", "Admin"
        FACULTY = "FACULTY", "Faculty"

    portal = models.CharField(max_length=10, choices=Portal.choices)
    code = models.CharField(max_length=64)
    label = models.CharField(max_length=100)
    icon = models.CharField(max_length=64, blank=True, null=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "menu_groups"
        ordering = ["portal", "sort_order", "id"]
        constraints = [
            models.UniqueConstraint(fields=["portal", "code"], name="uq_menu_groups_portal_code"),
        ]

    def __str__(self):
        return f"{self.portal}:{self.label}"


class MenuItem(TimeStampedModel, ActivatableModel):
    menu_group = models.ForeignKey("navigation.MenuGroup", on_delete=models.PROTECT, related_name="items")
    portal = models.CharField(max_length=10, choices=MenuGroup.Portal.choices)
    code = models.CharField(max_length=64)
    label = models.CharField(max_length=100)
    route_name = models.CharField(max_length=150, blank=True, null=True)
    icon = models.CharField(max_length=64, blank=True, null=True)
    parent = models.ForeignKey(
        "navigation.MenuItem", on_delete=models.PROTECT, related_name="children", blank=True, null=True
    )
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "menu_items"
        ordering = ["menu_group", "sort_order", "id"]
        constraints = [
            models.UniqueConstraint(fields=["portal", "code"], name="uq_menu_items_portal_code"),
        ]

    def __str__(self):
        return f"{self.portal}:{self.label}"


class MenuItemPermission(models.Model):
    menu_item = models.ForeignKey(
        "navigation.MenuItem", on_delete=models.PROTECT, related_name="menuitempermission_set"
    )
    permission = models.ForeignKey(
        "rbac.Permission", on_delete=models.PROTECT, related_name="menuitempermission_set"
    )

    class Meta:
        db_table = "menu_item_permissions"
        constraints = [
            models.UniqueConstraint(fields=["menu_item", "permission"], name="uq_menu_item_permissions"),
        ]

    def __str__(self):
        return f"{self.menu_item.code}:{self.permission.code}"

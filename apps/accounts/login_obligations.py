"""Account-first serialization for writes used by login-disable exceptions.

No role/loading operation enables login or advances its revocation version.
Shared catalogue updates lock accounts in ID order; direct assignments lock
their old/new accounts. Workers never lock obligation rows in the reverse order.
"""
from functools import wraps
import time

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import DEFAULT_DB_ALIAS, OperationalError, connections, models, router, transaction
from django.db.models import F


def retry_login_write(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        owner = args[0] if args else None
        alias = kwargs.get("using") or getattr(owner, "_db", None) or getattr(getattr(owner, "_state", None), "db", None) or DEFAULT_DB_ALIAS
        db = connections[alias]
        nested = db.in_atomic_block
        for attempt in range(6):
            try:
                return fn(*args, **kwargs)
            except OperationalError as exc:
                # Retry only a complete transaction, never an outer writer's
                # partly completed unit (e.g. import or account provisioning).
                if nested or db.vendor != "sqlite" or "locked" not in str(exc).lower() or attempt == 5:
                    raise
                time.sleep(0.02 * (2 ** attempt))
    return wrapped


def lock_login_users(user_ids, *, using=DEFAULT_DB_ALIAS, write=True, nowait=False):
    """Caller owns atomic(); all account locks precede tenant/event locks."""
    ids = sorted({pk for pk in user_ids if pk is not None})
    users = get_user_model().objects.using(using).filter(pk__in=ids).order_by("pk")
    if ids and write and connections[using].vendor == "sqlite":
        # SQLite ignores FOR UPDATE. A no-op write obtains its writer lock,
        # without changing access state, timestamps or session versions.
        users.update(login_access_version=F("login_access_version"))
    return {user.pk: user for user in users.select_for_update(nowait=nowait)}


class LoginObligationQuerySet(models.QuerySet):
    def _subject_ids(self, *, objects=(), changes=None):
        User = get_user_model()
        field = self.model.login_subject_field
        if field is None:
            # Roles/permissions/offerings can affect more than one account.
            # Lock the shared catalogue's account domain in a single order.
            return User.objects.using(self.db).values_list("pk", flat=True)
        ids = set(self.values_list(field, flat=True))
        ids.update(getattr(obj, field) for obj in objects)
        for key, value in (changes or {}).items():
            if key in {field, field.removesuffix("_id")}:
                if hasattr(value, "resolve_expression") or callable(value):
                    return User.objects.using(self.db).values_list("pk", flat=True)
                ids.add(getattr(value, "pk", value))
        return ids

    def _lock_subjects(self, *, objects=(), changes=None):
        ids = set(self._subject_ids(objects=objects, changes=changes))
        lock_login_users(ids, using=self.db)
        field = self.model.login_subject_field
        if field:
            # An assignment can move while we wait for its old account lock.
            # A current locking read also works under repeatable-read isolation.
            # Reject a changed owner instead of taking user locks out of order.
            current = set(self.select_for_update().values_list(field, flat=True))
            if not current.issubset(ids):
                raise ValidationError("The assigned account changed concurrently. Reload and retry the operation.")

    @retry_login_write
    def update(self, **kwargs):
        if not self.model.login_obligation_fields.intersection(kwargs):
            return super().update(**kwargs)
        with transaction.atomic(using=self.db):
            self._lock_subjects(changes=kwargs)
            return super().update(**kwargs)

    @retry_login_write
    def bulk_create(self, objs, *args, **kwargs):
        objs = list(objs)
        # Shared catalogue insertion has no existing consumers, except a
        # direct grant; those models name their account FK above.
        with transaction.atomic(using=self.db):
            if kwargs.get("update_conflicts") or args:
                lock_login_users(get_user_model().objects.using(self.db).values_list("pk", flat=True), using=self.db)
            elif self.model.login_subject_field is not None:
                self.none()._lock_subjects(objects=objs)
            return super().bulk_create(objs, *args, **kwargs)

    @retry_login_write
    def update_or_create(self, defaults=None, create_defaults=None, **kwargs):
        with transaction.atomic(using=self.db):
            changes = {**kwargs, **(defaults or {}), **(create_defaults or {})}
            self.filter(**kwargs)._lock_subjects(changes=changes)
            return super().update_or_create(defaults=defaults, create_defaults=create_defaults, **kwargs)

    @retry_login_write
    def bulk_update(self, objs, fields, *args, **kwargs):
        objs = list(objs)
        if not self.model.login_obligation_fields.intersection(fields):
            return super().bulk_update(objs, fields, *args, **kwargs)
        with transaction.atomic(using=self.db):
            targets = self.filter(pk__in=[obj.pk for obj in objs])
            subject_field = self.model.login_subject_field
            if subject_field and {subject_field, subject_field.removesuffix("_id")}.intersection(fields):
                ids = get_user_model().objects.using(self.db).values_list("pk", flat=True)
                lock_login_users(ids, using=self.db)
            else:
                targets._lock_subjects(objects=objs)
            return super().bulk_update(objs, fields, *args, **kwargs)


class LoginObligationModel(models.Model):
    """Covers ModelForms, provisioning, imports, loading and ORM bulk writes."""
    objects = LoginObligationQuerySet.as_manager()
    login_subject_field = None
    login_obligation_fields = frozenset()

    class Meta:
        abstract = True

    @retry_login_write
    def save(self, *args, **kwargs):
        fields = kwargs.get("update_fields")
        if fields is not None and not self.login_obligation_fields.intersection(fields):
            return super().save(*args, **kwargs)
        if self._state.adding and self.login_subject_field is None:
            return super().save(*args, **kwargs)
        using = kwargs.get("using") or router.db_for_write(type(self), instance=self)
        with transaction.atomic(using=using):
            rows = type(self).objects.using(using).filter(pk=self.pk)
            rows._lock_subjects(objects=[self])
            return super().save(*args, **kwargs)

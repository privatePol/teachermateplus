from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_datetime

from apps.core.services.api_keys import TenantApiKeyService
from apps.tenants.models import Tenant


class Command(BaseCommand):
    help = "Create a tenant-bound SIS API key and print the raw token once."

    def add_arguments(self, parser):
        parser.add_argument("--tenant-code", required=True)
        parser.add_argument("--name", required=True)
        parser.add_argument("--expires-at", default="")

    def handle(self, *args, **options):
        tenant_code = options["tenant_code"].strip()
        name = options["name"].strip()
        expires_at = None
        if options.get("expires_at"):
            expires_at = parse_datetime(options["expires_at"])
            if expires_at is None:
                raise CommandError("--expires-at must be an ISO datetime.")
        try:
            tenant = Tenant.objects.get(code=tenant_code, is_active=True)
        except Tenant.DoesNotExist as exc:
            raise CommandError(f"Active tenant not found: {tenant_code}") from exc
        row, raw_token = TenantApiKeyService.create_key(
            tenant=tenant,
            name=name,
            expires_at=expires_at,
        )
        self.stdout.write(self.style.SUCCESS(f"Created SIS API key {row.key_prefix} for {tenant.code}."))
        self.stdout.write("Store this token securely now; it will not be shown again:")
        self.stdout.write(raw_token)

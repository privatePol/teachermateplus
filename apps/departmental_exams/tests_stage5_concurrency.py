from unittest import skipUnless

from django.db import connection
from django.test import TransactionTestCase


class Stage5MariaDBConcurrencyTests(TransactionTestCase):
    @skipUnless(
        connection.vendor == "mysql",
        "SQLite cannot prove MariaDB row-lock scheduling for Stage 5 mutation races.",
    )
    def test_parent_first_row_lock_scheduling_requires_mariadb(self):
        self.assertEqual(connection.vendor, "mysql")

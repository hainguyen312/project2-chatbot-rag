"""
Cấp / thu hồi vai trò cho một email (MongoDB user_roles).

  .venv/bin/python -W ignore scripts/set_role.py --email a@b.com --role admin
  .venv/bin/python -W ignore scripts/set_role.py --email a@b.com --revoke
  .venv/bin/python -W ignore scripts/set_role.py --list
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env"))
from services.roles import VALID_ROLES, RoleStore  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--email")
ap.add_argument("--role", choices=VALID_ROLES)
ap.add_argument("--revoke", action="store_true")
ap.add_argument("--list", action="store_true")
a = ap.parse_args()

store = RoleStore.from_env()
if store is None:
    sys.exit("Thiếu MONGODB_URI trong .env")
if a.email and (a.role or a.revoke):
    store.set_role(a.email, None if a.revoke else a.role)
    print(f"{'Thu hồi' if a.revoke else 'Cấp'} quyền: {a.email.strip().lower()}")
elif not a.list:
    ap.error("cần --email kèm --role/--revoke, hoặc --list")
for r in store.list_roles():
    print(f"  {r['email']}: {r['role']}")
